#!/usr/bin/env python3
"""Gera os dados do painel (pasta data/) a partir dos dados abertos do TSE.

    python3 scripts/gerar_dados.py --ano 2026                  # todos os estados + exterior
    python3 scripts/gerar_dados.py --ano 2026 --uf PI --uf CE  # só alguns estados
    python3 scripts/gerar_dados.py --ano 2024 --uf PI --municipio TERESINA

Filtros opcionais (podem ser repetidos): --cargo Governador, --turno 1, --municipio TERESINA.

Cada estado vira uma pasta data/<UF>/ com um arquivo por cargo; o painel só baixa o
estado e o cargo que estão sendo vistos. data/estados.js lista todos os estados já
gerados, então dá para gerar um estado de cada vez sem perder os anteriores.

Arquivos do TSE usados (ficam em .cache-tse/ e só são baixados de novo quando o TSE
publica uma versão nova, por exemplo depois do 2º turno):
  votacao_secao_<ano>_<UF>.zip         votos por seção dos cargos do estado (ou municipais)
  votacao_secao_<ano>_BR.zip           votos para presidente no país todo (eleições gerais)
  eleitorado_local_votacao_<ano>.zip   nome e endereço das escolas, quando faltam no arquivo de votos

Só usa a biblioteca padrão do Python 3.
"""
import argparse
import csv
import glob
import io
import json
import os
import re
import shutil
import sys
import unicodedata
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from datetime import datetime

URL_TSE = 'https://cdn.tse.jus.br/estatistica/sead/odsele'
URL_VOTOS = URL_TSE + '/votacao_secao/votacao_secao_{ano}_{uf}.zip'
URL_LOCAIS = URL_TSE + '/eleitorado_locais_votacao/eleitorado_local_votacao_{ano}.zip'

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(RAIZ, '.cache-tse')
DADOS = os.path.join(RAIZ, 'data')

ESTADOS = {
    'AC': 'Acre', 'AL': 'Alagoas', 'AP': 'Amapá', 'AM': 'Amazonas', 'BA': 'Bahia', 'CE': 'Ceará',
    'DF': 'Distrito Federal', 'ES': 'Espírito Santo', 'GO': 'Goiás', 'MA': 'Maranhão',
    'MT': 'Mato Grosso', 'MS': 'Mato Grosso do Sul', 'MG': 'Minas Gerais', 'PA': 'Pará',
    'PB': 'Paraíba', 'PR': 'Paraná', 'PE': 'Pernambuco', 'PI': 'Piauí', 'RJ': 'Rio de Janeiro',
    'RN': 'Rio Grande do Norte', 'RS': 'Rio Grande do Sul', 'RO': 'Rondônia', 'RR': 'Roraima',
    'SC': 'Santa Catarina', 'SP': 'São Paulo', 'SE': 'Sergipe', 'TO': 'Tocantins', 'ZZ': 'Exterior',
}

NULO = '#NULO#'
ESPECIAIS = {'95', '96', '97', '98'}  # branco, nulo e anulados
TIPO_CANDIDATO, TIPO_LEGENDA, TIPO_ESPECIAL = 0, 1, 2
BITS = 20                     # votos guardados com a chave (local << BITS) | votável
MASCARA = (1 << BITS) - 1

csv.field_size_limit(sys.maxsize)


class ErroTSE(Exception):
    pass


def normaliza(texto):
    sem_acento = unicodedata.normalize('NFD', texto or '')
    return ''.join(ch for ch in sem_acento if unicodedata.category(ch) != 'Mn').upper().strip()


def milhar(n):
    return f'{n:,}'.replace(',', '.')


def vazio(valor):
    return not valor or valor.strip() in ('', NULO, '-1')


def data_hora(texto):
    try:
        return datetime.strptime(texto, '%d/%m/%Y %H:%M:%S')
    except ValueError:
        return datetime.min


# ---------------------------------------------------------------- download


def baixar(url, obrigatorio=True):
    """Baixa para .cache-tse/, reaproveitando a cópia local enquanto o TSE não publicar outra."""
    os.makedirs(CACHE, exist_ok=True)
    destino = os.path.join(CACHE, url.rsplit('/', 1)[1])
    marca = destino + '.versao'
    nome = os.path.basename(destino)

    versao = tamanho = None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method='HEAD'), timeout=60) as resposta:
            tamanho = int(resposta.headers.get('Content-Length') or 0)
            versao = f"{tamanho} {resposta.headers.get('Last-Modified')}"
    except urllib.error.HTTPError as erro:
        if erro.code in (403, 404):
            if obrigatorio:
                raise ErroTSE(f'{nome} não existe no TSE ({url})')
            return None
        if not os.path.exists(destino):
            raise ErroTSE(f'falha ao consultar {url}: {erro}')
    except urllib.error.URLError as erro:
        if not os.path.exists(destino):
            raise ErroTSE(f'sem acesso a {url}: {erro.reason}')

    if os.path.exists(destino):
        anterior = open(marca).read() if os.path.exists(marca) else None
        if versao is None or anterior == versao or (anterior is None and os.path.getsize(destino) == tamanho):
            if versao:
                with open(marca, 'w') as arquivo:
                    arquivo.write(versao)
            return destino

    print(f'baixando {nome} ({tamanho / 1e6:.0f} MB)', flush=True)
    temporario = destino + '.part'
    try:
        with urllib.request.urlopen(url, timeout=120) as resposta, open(temporario, 'wb') as arquivo:
            shutil.copyfileobj(resposta, arquivo, 1 << 20)
    except Exception as erro:
        if os.path.exists(temporario):
            os.remove(temporario)
        raise ErroTSE(f'falha ao baixar {url}: {erro}')
    os.replace(temporario, destino)
    with open(marca, 'w') as arquivo:
        arquivo.write(versao)
    return destino


# ---------------------------------------------------------------- leitura


def tabelas(caminho, so_ufs=None):
    """(cabeçalho, leitor) de cada .csv de um .zip do TSE, ou de um .csv solto.

    Com `so_ufs`, lê só os .csv terminados em _<UF>.csv (o zip de locais traz um
    arquivo por estado e mais um com o Brasil inteiro)."""
    if not caminho.lower().endswith('.zip'):
        with open(caminho, encoding='latin-1', newline='') as texto:
            leitor = csv.reader(texto, delimiter=';')
            yield next(leitor), leitor
        return
    with zipfile.ZipFile(caminho) as pacote:
        nomes = [n for n in pacote.namelist() if n.lower().endswith('.csv')]
        if so_ufs:
            do_estado = [n for n in nomes if any(n.upper().endswith(f'_{uf}.CSV') for uf in so_ufs)]
            nomes = do_estado or nomes
        for nome in nomes:
            with pacote.open(nome) as bruto:
                leitor = csv.reader(io.TextIOWrapper(bruto, encoding='latin-1', newline=''), delimiter=';')
                cabecalho = next(leitor, None)
                if cabecalho:
                    yield cabecalho, leitor


class Colunas:
    """Posição de cada coluna usada no arquivo de votação por seção."""
    NOMES = {
        'uf': 'SG_UF', 'ano': 'ANO_ELEICAO', 'cd_eleicao': 'CD_ELEICAO', 'ds_eleicao': 'DS_ELEICAO',
        'turno': 'NR_TURNO', 'cd_cargo': 'CD_CARGO', 'cargo': 'DS_CARGO', 'ue': 'SG_UE', 'nm_ue': 'NM_UE',
        'cd_mun': 'CD_MUNICIPIO', 'nm_mun': 'NM_MUNICIPIO', 'zona': 'NR_ZONA', 'local': 'NR_LOCAL_VOTACAO',
        'nm_local': 'NM_LOCAL_VOTACAO', 'end_local': 'DS_LOCAL_VOTACAO_ENDERECO',
        'numero': 'NR_VOTAVEL', 'nome': 'NM_VOTAVEL', 'votos': 'QT_VOTOS', 'data': 'DT_GERACAO', 'hora': 'HH_GERACAO',
    }

    def __init__(self, cabecalho):
        posicao = {nome: i for i, nome in enumerate(cabecalho)}
        for atributo, coluna in self.NOMES.items():
            setattr(self, atributo, posicao.get(coluna))
        faltando = [c for a, c in self.NOMES.items() if getattr(self, a) is None and a not in ('nm_local', 'end_local')]
        if faltando:
            raise ErroTSE(f'colunas ausentes no arquivo de votação: {", ".join(faltando)}')


class Estado:
    """Votos de um estado somados por local de votação."""

    def __init__(self, uf):
        self.uf = uf
        self.municipios = {}   # código -> nome
        self.locais = {}       # (município, zona, nº do local) -> índice em self.info
        self.info = []         # [município, zona, nº do local, nome, endereço]
        self.eleicoes = {}     # (ano, eleição, turno, cargo) -> dict
        self.geracoes = set()

    def local(self, cd_mun, zona, numero, nome_mun):
        chave = (cd_mun, zona, numero)
        l = self.locais.get(chave)
        if l is None:
            l = self.locais[chave] = len(self.info)
            self.info.append([cd_mun, zona, numero, '', ''])
            self.municipios.setdefault(cd_mun, nome_mun)
        return l

    def eleicao(self, chave, ano, descricao, turno, cd_cargo, cargo):
        e = self.eleicoes.get(chave)
        if e is None:
            e = self.eleicoes[chave] = {
                'ano': ano, 'descricao': descricao, 'turno': turno, 'cd_cargo': cd_cargo, 'cargo': cargo,
                'indice': {}, 'votaveis': [], 'votos': {},
            }
        return e

    @staticmethod
    def votavel(e, numero, nome, sg_ue, nm_ue):
        # branco/nulo é o mesmo "votável" em todas as cidades; candidatos dependem da UE
        chave = ('', numero) if numero in ESPECIAIS else (sg_ue, numero)
        v = e['indice'].get(chave)
        if v is None:
            v = e['indice'][chave] = len(e['votaveis'])
            e['votaveis'].append([numero, nome, '' if numero in ESPECIAIS else nm_ue, chave])
        return v

    def adicionar(self, r, c):
        l = self.local(int(r[c.cd_mun]), int(r[c.zona]), int(r[c.local]), r[c.nm_mun])
        info = self.info[l]
        if not info[3] and c.nm_local is not None and not vazio(r[c.nm_local]):
            info[3] = r[c.nm_local].strip()
        if not info[4] and c.end_local is not None and not vazio(r[c.end_local]):
            info[4] = r[c.end_local].strip()
        e = self.eleicao((r[c.ano], r[c.cd_eleicao], r[c.turno], r[c.cd_cargo]),
                         r[c.ano], r[c.ds_eleicao], r[c.turno], int(r[c.cd_cargo]), r[c.cargo])
        v = self.votavel(e, r[c.numero], r[c.nome], r[c.ue], r[c.nm_ue])
        k = (l << BITS) | v
        e['votos'][k] = e['votos'].get(k, 0) + int(r[c.votos])
        self.geracoes.add((r[c.data], r[c.hora]))

    def juntar(self, outro):
        """Soma os dados de outro Estado (ex.: os votos para presidente vindos do arquivo BR)."""
        mapa_local = []
        for cd_mun, zona, numero, nome, endereco in outro.info:
            l = self.local(cd_mun, zona, numero, outro.municipios[cd_mun])
            self.info[l][3] = self.info[l][3] or nome
            self.info[l][4] = self.info[l][4] or endereco
            mapa_local.append(l)
        for chave, e in outro.eleicoes.items():
            destino = self.eleicao(chave, e['ano'], e['descricao'], e['turno'], e['cd_cargo'], e['cargo'])
            mapa_v = [self.votavel(destino, numero, nome, ch[0], ue) for numero, nome, ue, ch in e['votaveis']]
            for k, q in e['votos'].items():
                nk = (mapa_local[k >> BITS] << BITS) | mapa_v[k & MASCARA]
                destino['votos'][nk] = destino['votos'].get(nk, 0) + q
        self.geracoes |= outro.geracoes


def ler_votos(caminho, estados, filtros, ufs=None):
    """Lê um arquivo de votação por seção, distribuindo as linhas entre os estados (SG_UF)."""
    lidas = 0
    cargos_ok = {}
    for cabecalho, leitor in tabelas(caminho):
        c = Colunas(cabecalho)
        for r in leitor:
            lidas += 1
            if lidas % 2_000_000 == 0:
                print(f'  {os.path.basename(caminho)}: {milhar(lidas)} linhas', flush=True)
            uf = r[c.uf]
            if ufs and uf not in ufs:
                continue
            if filtros['turno'] and r[c.turno] not in filtros['turno']:
                continue
            if filtros['cargo']:
                cargo = r[c.cargo]
                if cargo not in cargos_ok:
                    cargos_ok[cargo] = normaliza(cargo) in filtros['cargo']
                if not cargos_ok[cargo]:
                    continue
            if filtros['municipio'] and normaliza(r[c.nm_mun]) not in filtros['municipio']:
                continue
            est = estados.get(uf)
            if est is None:
                est = estados[uf] = Estado(uf)
            est.adicionar(r, c)
    return lidas


def faltam_nomes(caminho, amostra=2000):
    """O arquivo de votos traz o nome das escolas? (nos dias seguintes à eleição, não traz)"""
    for cabecalho, leitor in tabelas(caminho):
        c = Colunas(cabecalho)
        if c.nm_local is None:
            return True
        for i, r in enumerate(leitor):
            if vazio(r[c.nm_local]):
                return True
            if i >= amostra:
                return False
    return False


def completar_locais(est, caminho):
    """Preenche nome/endereço dos locais a partir do arquivo de eleitorado por local de votação."""
    faltando = {k for k, l in est.locais.items() if not est.info[l][3] or not est.info[l][4]}
    if not faltando:
        return
    # Quando um local muda de prédio, o arquivo de votos pode manter o número antigo,
    # que aparece em NR_LOCAL_VOTACAO_ORIGINAL; usado só se o número atual não bater.
    pelo_original = {}
    for cabecalho, leitor in tabelas(caminho, {est.uf}):
        p = {nome: i for i, nome in enumerate(cabecalho)}
        if not {'CD_MUNICIPIO', 'NR_ZONA', 'NR_LOCAL_VOTACAO', 'NM_LOCAL_VOTACAO'} <= p.keys():
            continue
        valor = lambda r, coluna: r[p[coluna]] if coluna in p else ''
        for r in leitor:
            try:
                chave = (int(r[p['CD_MUNICIPIO']]), int(r[p['NR_ZONA']]), int(r[p['NR_LOCAL_VOTACAO']]))
            except ValueError:
                continue
            if chave in faltando:
                preencher_local(est.info[est.locais[chave]], valor(r, 'NM_LOCAL_VOTACAO'),
                                valor(r, 'DS_ENDERECO'), valor(r, 'NM_BAIRRO'))
            try:
                original = (chave[0], chave[1], int(valor(r, 'NR_LOCAL_VOTACAO_ORIGINAL')))
            except ValueError:
                continue
            if original != chave and original in faltando and original not in pelo_original:
                pelo_original[original] = (valor(r, 'NM_LOCAL_VOTACAO_ORIGINAL'), valor(r, 'DS_ENDERECO_LOCVT_ORIGINAL'))
    for chave, (nome, endereco) in pelo_original.items():
        preencher_local(est.info[est.locais[chave]], nome, endereco)


def preencher_local(info, nome, endereco, bairro=None):
    if not info[3] and not vazio(nome):
        info[3] = nome.strip()
    if not info[4]:
        info[4] = ' - '.join(p.strip() for p in (endereco, bairro) if not vazio(p))


# ---------------------------------------------------------------- gravação


def slug(texto):
    return re.sub(r'[^a-z0-9]+', '-', normaliza(texto).lower()).strip('-')


def escrever_js(pasta, uf, nome, conteudo):
    caminho = os.path.join(pasta, nome + '.js')
    with open(caminho, 'w', encoding='utf-8') as arquivo:
        arquivo.write('// Gerado por scripts/gerar_dados.py - não edite à mão.\n')
        arquivo.write(f'window.registrarDados({json.dumps(uf + "/" + nome)}, ')
        json.dump(conteudo, arquivo, ensure_ascii=False, separators=(',', ':'))
        arquivo.write(');\n')
    return os.path.getsize(caminho)


def gravar(est):
    pasta = os.path.join(DADOS, est.uf)
    if os.path.isdir(pasta):
        shutil.rmtree(pasta)  # tira cargos que não existem mais
    os.makedirs(pasta)

    cods = sorted(est.municipios, key=lambda cd: normaliza(est.municipios[cd]))
    idx_mun = {cd: i for i, cd in enumerate(cods)}
    ordem = sorted(range(len(est.info)), key=lambda l: (
        idx_mun[est.info[l][0]], est.info[l][1], normaliza(est.info[l][3]), est.info[l][2]))
    novo_local = [0] * len(ordem)
    for novo, antigo in enumerate(ordem):
        novo_local[antigo] = novo
    base = {
        'municipios': [est.municipios[cd] for cd in cods],
        'locais': [[idx_mun[cd], str(zona), nome or f'LOCAL {numero}', endereco]
                   for cd, zona, numero, nome, endereco in (est.info[l] for l in ordem)],
    }
    bytes_base = escrever_js(pasta, est.uf, 'base', base)

    cargos = []
    ids = set()
    for chave in sorted(est.eleicoes, key=lambda k: (k[0], int(k[2]), est.eleicoes[k]['cd_cargo'], k[1])):
        e = est.eleicoes[chave]
        totais = [0] * len(e['votaveis'])
        for k, q in e['votos'].items():
            totais[k & MASCARA] += q
        proporcional = any(len(v[0]) > 2 for v in e['votaveis'])
        tipos = [TIPO_ESPECIAL if v[0] in ESPECIAIS else
                 TIPO_LEGENDA if proporcional and len(v[0]) == 2 else TIPO_CANDIDATO for v in e['votaveis']]
        ordem_v = sorted(range(len(e['votaveis'])), key=lambda v: (tipos[v] == TIPO_ESPECIAL, -totais[v]))
        novo_v = [0] * len(ordem_v)
        for novo, antigo in enumerate(ordem_v):
            novo_v[antigo] = novo

        # votos agrupados por local: [local, quantidade de pares, candidato, votos, candidato, votos, ...]
        por_local = {}
        for k, q in e['votos'].items():
            por_local.setdefault(novo_local[k >> BITS], []).append((novo_v[k & MASCARA], q))
        votos = []
        for l in sorted(por_local):
            pares = sorted(por_local[l])
            votos.append(l)
            votos.append(len(pares))
            for par in pares:
                votos.extend(par)

        rotulo = f"{e['cargo']} · {e['turno']}º turno"
        id_cargo = slug(f"{e['cargo']}-{e['turno']}t")
        if id_cargo in ids:  # mesmo cargo em outra eleição (suplementar etc.)
            id_cargo = slug(f"{id_cargo}-{e['ano']}-{chave[1]}")
            rotulo = f"{rotulo} ({e['descricao']})"
        ids.add(id_cargo)
        candidatos = [[*e['votaveis'][v][:3], tipos[v]] for v in ordem_v]
        tamanho = escrever_js(pasta, est.uf, id_cargo, {'rotulo': rotulo, 'candidatos': candidatos, 'votos': votos})
        cargos.append({'id': id_cargo, 'rotulo': rotulo, 'bytes': tamanho})

    anos = sorted({e['ano'] for e in est.eleicoes.values()})
    geracao = max(est.geracoes, key=lambda dh: data_hora(' '.join(dh)), default=('', ''))
    info = {
        'uf': est.uf,
        'nome': ESTADOS.get(est.uf, est.uf),
        'titulo': f"Eleições {'/'.join(anos)} · {ESTADOS.get(est.uf, est.uf)}",
        'gerado': f'{geracao[0]} {geracao[1][:5]}'.strip(),
        'cidades': len(cods),
        'locais': len(ordem),
        'base': {'bytes': bytes_base},
        'cargos': cargos,
    }
    with open(os.path.join(pasta, 'info.json'), 'w', encoding='utf-8') as arquivo:
        json.dump(info, arquivo, ensure_ascii=False, indent=1)
    sem_nome = sum(1 for i in est.info if not i[3])
    total = bytes_base + sum(c['bytes'] for c in cargos)
    return f"{est.uf}: {len(cods)} cidades, {len(ordem)} locais, {len(cargos)} cargos, {total / 1e6:.1f} MB" + \
        (f' (atenção: {sem_nome} locais sem nome)' if sem_nome else '')


def atualizar_indice():
    """Reescreve data/estados.js com todos os estados presentes em data/."""
    estados = []
    for caminho in glob.glob(os.path.join(DADOS, '*', 'info.json')):
        with open(caminho, encoding='utf-8') as arquivo:
            estados.append(json.load(arquivo))
    estados.sort(key=lambda e: (e['uf'] == 'ZZ', normaliza(e['nome'])))
    indice = {'fonte': 'TSE · Dados abertos: votação por seção eleitoral', 'estados': estados}
    with open(os.path.join(DADOS, 'estados.js'), 'w', encoding='utf-8') as arquivo:
        arquivo.write('// Gerado por scripts/gerar_dados.py - não edite à mão.\n')
        arquivo.write('window.ESTADOS_ELEICAO = ')
        json.dump(indice, arquivo, ensure_ascii=False, separators=(',', ':'))
        arquivo.write(';\n')
    return len(estados)


# ---------------------------------------------------------------- execução


def processar_estado(uf, arquivo, parcial, filtros, arquivo_locais):
    """Roda em um processo separado: lê o arquivo do estado, junta presidente e grava data/<UF>/."""
    est = Estado(uf)
    if arquivo:
        ler_votos(arquivo, {uf: est}, filtros, {uf})
    if parcial:
        est.juntar(parcial)
    if not est.eleicoes:
        return f'{uf}: nenhum voto com esses filtros'
    if arquivo_locais:
        completar_locais(est, arquivo_locais)
    return gravar(est)


def main():
    p = argparse.ArgumentParser(description='Gera a pasta data/ do painel a partir dos dados abertos do TSE.')
    p.add_argument('--ano', help='ano da eleição; baixa os arquivos do TSE')
    p.add_argument('--uf', action='append', help='estado (pode repetir); sem --uf, gera todos')
    p.add_argument('--arquivo', action='append', default=[], help='votacao_secao_*.zip já baixado (pode repetir)')
    p.add_argument('--locais', help='eleitorado_local_votacao_*.zip já baixado')
    p.add_argument('--sem-presidente', action='store_true', help='não incluir os votos para presidente (arquivo BR)')
    p.add_argument('--cargo', action='append', help='manter só este cargo, ex.: Governador (pode repetir)')
    p.add_argument('--turno', action='append', help='manter só este turno: 1 ou 2 (pode repetir)')
    p.add_argument('--municipio', action='append', help='manter só esta cidade (pode repetir)')
    p.add_argument('--processos', type=int, default=min(4, os.cpu_count() or 1), help='estados processados ao mesmo tempo')
    a = p.parse_args()
    if not a.ano and not a.arquivo:
        p.error('informe --ano (para baixar do TSE) ou --arquivo')

    ufs = sorted({u.upper() for u in a.uf}) if a.uf else None
    for uf in ufs or []:
        if uf not in ESTADOS:
            p.error(f'estado desconhecido: {uf}')
    filtros = {
        'turno': set(a.turno or []),
        'cargo': {normaliza(c) for c in a.cargo or []},
        'municipio': {normaliza(m) for m in a.municipio or []},
    }

    try:
        # 1. downloads (vários ao mesmo tempo)
        por_estado, compartilhados = {}, list(a.arquivo)
        if a.ano:
            alvo = ufs or sorted(ESTADOS)
            with ThreadPoolExecutor(4) as fila:
                tarefas = {fila.submit(baixar, URL_VOTOS.format(ano=a.ano, uf=uf), False): uf for uf in alvo}
                if not a.sem_presidente:
                    tarefas[fila.submit(baixar, URL_VOTOS.format(ano=a.ano, uf='BR'), False)] = 'BR'
                for tarefa in as_completed(tarefas):
                    uf, caminho = tarefas[tarefa], tarefa.result()
                    if uf == 'BR':
                        if caminho:
                            compartilhados.append(caminho)
                    elif caminho:
                        por_estado[uf] = caminho
                    else:
                        print(f'{uf}: o TSE não tem arquivo de {a.ano} para este estado')

        # 2. arquivos com vários estados (presidente / --arquivo), lidos uma vez e separados por UF
        parciais = {}
        for caminho in compartilhados:
            print(f'lendo {os.path.basename(caminho)}', flush=True)
            ler_votos(caminho, parciais, filtros, set(ufs) if ufs else None)
        todos = sorted(set(por_estado) | set(parciais))
        if not todos:
            sys.exit('nenhum voto encontrado')

        # 3. nome das escolas
        arquivo_locais = a.locais
        amostras = list(por_estado.values()) or compartilhados
        if not arquivo_locais and a.ano and any(faltam_nomes(c) for c in amostras[:3]):
            arquivo_locais = baixar(URL_LOCAIS.format(ano=a.ano))

        # 4. um estado por processo, maiores primeiro
        ordem = sorted(todos, key=lambda uf: -os.path.getsize(por_estado[uf]) if uf in por_estado else 0)
        print(f'processando {len(ordem)} estado(s) com {a.processos} processo(s)...', flush=True)
        with ProcessPoolExecutor(max(1, a.processos)) as pool:
            tarefas = {pool.submit(processar_estado, uf, por_estado.get(uf), parciais.pop(uf, None), filtros, arquivo_locais): uf
                       for uf in ordem}
            erros = 0
            for tarefa in as_completed(tarefas):
                try:
                    print(tarefa.result(), flush=True)
                except Exception as erro:
                    erros += 1
                    print(f'{tarefas[tarefa]}: ERRO {erro}', flush=True)
    except ErroTSE as erro:
        sys.exit(str(erro))

    total = atualizar_indice()
    print(f'ok: data/estados.js com {total} estado(s)' + (f' ({erros} com erro)' if erros else ''))
    if os.path.exists(os.path.join(DADOS, 'dados.js')):
        os.remove(os.path.join(DADOS, 'dados.js'))  # formato antigo, de um arquivo só


if __name__ == '__main__':
    main()
