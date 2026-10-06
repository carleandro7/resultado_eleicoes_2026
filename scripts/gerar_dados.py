#!/usr/bin/env python3
"""Gera os dados do painel (pasta data/) a partir dos dados abertos do TSE.

    python3 scripts/gerar_dados.py --ano 2026                  # todos os estados + exterior
    python3 scripts/gerar_dados.py --ano 2010-2026             # todas as eleições de 2010 a 2026
    python3 scripts/gerar_dados.py --ano 2024 --uf PI --uf CE  # só alguns estados
    python3 scripts/gerar_dados.py --ano 2024 --uf PI --municipio TERESINA

Filtros opcionais (podem ser repetidos): --cargo Governador, --turno 1, --municipio TERESINA.

Cada eleição e estado vira uma pasta data/<ano>/<UF>/ com um arquivo por cargo; o painel só
baixa o ano, o estado e o cargo que estão sendo vistos. data/eleicoes.js lista tudo o que já
foi gerado, então dá para gerar um ano ou um estado de cada vez sem perder os anteriores.

Arquivos do TSE usados (ficam em .cache-tse/ e só são baixados de novo quando o TSE
publica uma versão nova, por exemplo depois do 2º turno):
  votacao_secao_<ano>_<UF>.zip         votos por seção dos cargos do estado (ou municipais)
  votacao_secao_<ano>_BR.zip           votos para presidente no país todo (eleições gerais)
  eleitorado_local_votacao_<ano>.zip   coordenadas das escolas (mapa) e nome/endereço quando faltam

Só usa a biblioteca padrão do Python 3.
"""
import argparse
import collections
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
    'VT': 'Voto em trânsito',  # 2010: presidente votado fora do estado, nas capitais; só vem no arquivo BR,
                               # em zonas especiais que não estão no cadastro de locais (fica sem mapa)
}
SEM_ARQUIVO_PROPRIO = {'VT'}

NULO = '#NULO#'
ESPECIAIS = {'95', '96', '97', '98'}  # branco, nulo e anulados
CARGOS_MUNICIPAIS = {11, 12, 13}      # prefeito, vice e vereador: cada cidade tem os seus candidatos
ORDINARIA = 'ordinaria'               # no lugar do código da eleição, juntando as ordinárias do ano
TIPO_CANDIDATO, TIPO_LEGENDA, TIPO_ESPECIAL = 0, 1, 2
BITS = 20                     # votos guardados com a chave (local << BITS) | votável
MASCARA = (1 << BITS) - 1
# Votos gravados como texto: cada número vira "dígitos" de 5 bits, do mais para o menos
# significativo; os 32 primeiros caracteres fecham o número e os 32 últimos dizem que ele continua.
ALFABETO = '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ-_'

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


def municipal(ano):
    return int(ano) % 4 == 0  # 2012, 2016, 2020, 2024...; as gerais caem nos outros anos pares


def nome_cargo(texto):
    """Em alguns anos o TSE escreve o cargo em maiúsculas ("VEREADOR"); fica "Vereador", como nos outros."""
    texto = re.sub(r'\s+', ' ', texto).strip()
    if texto != texto.upper():
        return texto
    return ' '.join(p if p in ('de', 'da', 'do') else p.capitalize() for p in texto.lower().split(' '))


def expandir_anos(valores):
    """'2026', '2010-2024' ou '2018,2022' -> anos com eleição (pares), do mais novo para o mais antigo."""
    anos = set()
    for valor in valores:
        for parte in valor.split(','):
            m = re.fullmatch(r'\s*(\d{4})\s*(?:-\s*(\d{4})\s*)?', parte)
            if not m:
                raise ValueError(f'ano inválido: {parte!r}')
            inicio, fim = sorted((int(m[1]), int(m[2] or m[1])))
            anos |= {str(a) for a in range(inicio, fim + 1) if a % 2 == 0}
    return sorted(anos, reverse=True)


# ---------------------------------------------------------------- download


def baixar(url, obrigatorio=True, usar_cache=False):
    """Baixa para .cache-tse/, reaproveitando a cópia local enquanto o TSE não publicar outra.

    Com usar_cache, a cópia local é usada sem perguntar ao TSE se há versão nova."""
    os.makedirs(CACHE, exist_ok=True)
    destino = os.path.join(CACHE, url.rsplit('/', 1)[1])
    marca = destino + '.versao'
    nome = os.path.basename(destino)
    if usar_cache and os.path.exists(destino):
        return destino

    consultou = False
    tamanho = publicado = None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method='HEAD'), timeout=60) as resposta:
            consultou = True
            # em arquivos grandes o CDN do TSE às vezes responde Content-Length 1: aí vale só a data
            tamanho = int(resposta.headers.get('Content-Length') or 0)
            tamanho = tamanho if tamanho > 1 else None
            publicado = resposta.headers.get('Last-Modified')
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
        if not consultou:
            return destino
        local = os.path.getsize(destino)
        anterior = open(marca).read().split(' ', 1) if os.path.exists(marca) else None
        mesma_data = anterior is None or (len(anterior) == 2 and anterior[1] == publicado)
        if tamanho in (None, local) and mesma_data:
            with open(marca, 'w') as arquivo:
                arquivo.write(f'{local} {publicado}')
            return destino

    print(f'baixando {nome}' + (f' ({tamanho / 1e6:.0f} MB)' if tamanho else ''), flush=True)
    temporario = destino + '.part'
    try:
        with urllib.request.urlopen(url, timeout=120) as resposta, open(temporario, 'wb') as arquivo:
            shutil.copyfileobj(resposta, arquivo, 1 << 20)
        if not zipfile.is_zipfile(temporario):
            raise ValueError('o arquivo veio incompleto ou não é um .zip')
    except Exception as erro:
        if os.path.exists(temporario):
            os.remove(temporario)
        raise ErroTSE(f'falha ao baixar {url}: {erro}')
    os.replace(temporario, destino)
    with open(marca, 'w') as arquivo:
        arquivo.write(f'{os.path.getsize(destino)} {publicado}')
    return destino


def apagar_baixado(caminho):
    for arquivo in (caminho, caminho + '.versao'):
        if os.path.exists(arquivo):
            os.remove(arquivo)


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
        'uf': 'SG_UF', 'ano': 'ANO_ELEICAO', 'tipo': 'CD_TIPO_ELEICAO', 'cd_eleicao': 'CD_ELEICAO', 'ds_eleicao': 'DS_ELEICAO',
        'turno': 'NR_TURNO', 'cd_cargo': 'CD_CARGO', 'cargo': 'DS_CARGO', 'ue': 'SG_UE', 'nm_ue': 'NM_UE',
        'cd_mun': 'CD_MUNICIPIO', 'nm_mun': 'NM_MUNICIPIO', 'zona': 'NR_ZONA', 'local': 'NR_LOCAL_VOTACAO',
        'nm_local': 'NM_LOCAL_VOTACAO', 'end_local': 'DS_LOCAL_VOTACAO_ENDERECO',
        'numero': 'NR_VOTAVEL', 'nome': 'NM_VOTAVEL', 'votos': 'QT_VOTOS', 'data': 'DT_GERACAO', 'hora': 'HH_GERACAO',
    }

    def __init__(self, cabecalho):
        posicao = {nome: i for i, nome in enumerate(cabecalho)}
        for atributo, coluna in self.NOMES.items():
            setattr(self, atributo, posicao.get(coluna))
        faltando = [c for a, c in self.NOMES.items() if getattr(self, a) is None and a not in ('nm_local', 'end_local', 'tipo')]
        if faltando:
            raise ErroTSE(f'colunas ausentes no arquivo de votação: {", ".join(faltando)}')


class Estado:
    """Votos de um estado numa eleição (ano), somados por local de votação."""

    def __init__(self, uf, ano):
        self.uf = uf
        self.ano = ano
        self.municipios = {}   # código -> nome
        self.locais = {}       # (município, zona, nº do local) -> índice em self.info
        self.info = []         # [município, zona, nº do local, nome, endereço, latitude, longitude, bairro]
        self.eleicoes = {}     # (ano, eleição, turno, cargo) -> dict
        self.geracoes = set()

    def local(self, cd_mun, zona, numero, nome_mun):
        chave = (cd_mun, zona, numero)
        l = self.locais.get(chave)
        if l is None:
            l = self.locais[chave] = len(self.info)
            self.info.append([cd_mun, zona, numero, '', '', None, None, ''])
            self.municipios.setdefault(cd_mun, nome_mun)
        return l

    def eleicao(self, chave, ano, descricao, turno, cd_cargo, cargo):
        e = self.eleicoes.get(chave)
        if e is None:
            e = self.eleicoes[chave] = {
                'ano': ano, 'descricao': descricao, 'turno': turno, 'cd_cargo': cd_cargo, 'cargo': nome_cargo(cargo),
                'indice': {}, 'votaveis': [], 'votos': {},
            }
        return e

    @staticmethod
    def votavel(e, numero, nome, sg_ue, nm_ue):
        # Branco/nulo é o mesmo "votável" em todas as cidades. Candidatos a prefeito e vereador
        # dependem da cidade (UE); os demais valem para o estado todo, mesmo nos anos em que o
        # TSE põe a cidade no SG_UE de governador, senador etc.
        if numero in ESPECIAIS:
            chave, ue = ('', numero), ''
        elif e['cd_cargo'] in CARGOS_MUNICIPAIS:
            chave, ue = (sg_ue, numero), nm_ue
        else:
            chave, ue = ('*', numero), ''
        v = e['indice'].get(chave)
        if v is None:
            v = e['indice'][chave] = len(e['votaveis'])
            e['votaveis'].append([numero, nome, ue, chave])
        return v

    def adicionar(self, r, c):
        l = self.local(int(r[c.cd_mun]), int(r[c.zona]), int(r[c.local]), r[c.nm_mun])
        info = self.info[l]
        if not info[3] and c.nm_local is not None and not vazio(r[c.nm_local]):
            info[3] = r[c.nm_local].strip()
        if not info[4] and c.end_local is not None and not vazio(r[c.end_local]):
            info[4] = r[c.end_local].strip()
        # As eleições ordinárias do mesmo cargo e turno viram uma só (em 2020 Macapá votou em dezembro,
        # numa eleição à parte); as suplementares, que refazem a votação de uma cidade, ficam separadas.
        ordinaria = c.tipo is not None and r[c.tipo] == '2'
        e = self.eleicao((r[c.ano], ORDINARIA if ordinaria else r[c.cd_eleicao], r[c.turno], r[c.cd_cargo]),
                         r[c.ano], r[c.ds_eleicao], r[c.turno], int(r[c.cd_cargo]), r[c.cargo])
        v = self.votavel(e, r[c.numero], r[c.nome], r[c.ue], r[c.nm_ue])
        k = (l << BITS) | v
        e['votos'][k] = e['votos'].get(k, 0) + int(r[c.votos])
        self.geracoes.add((r[c.data], r[c.hora]))

    def juntar(self, outro):
        """Soma os dados de outro Estado (ex.: os votos para presidente vindos do arquivo BR)."""
        mapa_local = []
        for cd_mun, zona, numero, *dados in outro.info:
            l = self.local(cd_mun, zona, numero, outro.municipios[cd_mun])
            info = self.info[l]
            for i, valor in enumerate(dados, start=3):
                if info[i] in ('', None):
                    info[i] = valor
            mapa_local.append(l)
        for chave, e in outro.eleicoes.items():
            destino = self.eleicao(chave, e['ano'], e['descricao'], e['turno'], e['cd_cargo'], e['cargo'])
            mapa_v = [self.votavel(destino, numero, nome, ch[0], ue) for numero, nome, ue, ch in e['votaveis']]
            for k, q in e['votos'].items():
                nk = (mapa_local[k >> BITS] << BITS) | mapa_v[k & MASCARA]
                destino['votos'][nk] = destino['votos'].get(nk, 0) + q
        self.geracoes |= outro.geracoes


def ler_votos(caminho, estados, filtros, ufs=None):
    """Lê um arquivo de votação por seção, distribuindo as linhas por (ano, estado)."""
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
            chave = (r[c.ano], uf)
            est = estados.get(chave)
            if est is None:
                est = estados[chave] = Estado(uf, r[c.ano])
            est.adicionar(r, c)
    return lidas


def completar_locais(est, caminho):
    """Preenche nome, endereço e coordenadas dos locais com o arquivo de eleitorado por local de votação."""
    achados = set()
    # Quando um local muda de prédio, o arquivo de votos pode manter o número antigo,
    # que aparece em NR_LOCAL_VOTACAO_ORIGINAL; usado só se o número atual não bater.
    # As coordenadas são as do prédio novo, onde os votos foram dados.
    pelo_original = {}
    for cabecalho, leitor in tabelas(caminho, {est.uf}):
        p = {nome: i for i, nome in enumerate(cabecalho)}
        if not {'CD_MUNICIPIO', 'NR_ZONA', 'NR_LOCAL_VOTACAO', 'NM_LOCAL_VOTACAO'} <= p.keys():
            continue
        valor = lambda r, coluna: r[p[coluna]] if coluna in p else ''
        col_uf = p.get('SG_UF')  # até 2024 o cadastro vem num arquivo só, com o Brasil inteiro
        for r in leitor:
            if col_uf is not None and r[col_uf] != est.uf:
                continue
            try:
                chave = (int(r[p['CD_MUNICIPIO']]), int(r[p['NR_ZONA']]), int(r[p['NR_LOCAL_VOTACAO']]))
            except ValueError:
                continue
            posicao = (coordenada(valor(r, 'NR_LATITUDE')), coordenada(valor(r, 'NR_LONGITUDE')))
            if chave in est.locais and chave not in achados:
                achados.add(chave)
                preencher_local(est.info[est.locais[chave]], valor(r, 'NM_LOCAL_VOTACAO'),
                                valor(r, 'DS_ENDERECO'), valor(r, 'NM_BAIRRO'), *posicao)
            try:
                original = (chave[0], chave[1], int(valor(r, 'NR_LOCAL_VOTACAO_ORIGINAL')))
            except ValueError:
                continue
            if original != chave and original in est.locais and original not in pelo_original:
                pelo_original[original] = (valor(r, 'NM_LOCAL_VOTACAO_ORIGINAL'), valor(r, 'DS_ENDERECO_LOCVT_ORIGINAL'),
                                           valor(r, 'NM_BAIRRO'), *posicao)
    for chave, dados in pelo_original.items():
        if chave not in achados:
            preencher_local(est.info[est.locais[chave]], *dados)


def chave_local(nome):
    return re.sub(r'[^A-Z0-9]+', ' ', normaliza(nome)).strip()


def no_brasil(lat, lon):
    return -34 <= lat <= 6 and -74 <= lon <= -28


_por_cadastro = {}  # arquivo de locais -> {(UF, município, nome do local): (lat, lon)}, lido uma vez por execução


def coordenadas_conhecidas():
    """{UF: {(município, nome do local): (lat, lon)}} juntando os cadastros de locais de todos os anos
    já baixados em .cache-tse/ (o mais novo vence). Até 2016 cerca de 30% das escolas vêm sem
    coordenadas no cadastro do próprio ano, mas a mesma escola costuma estar nos cadastros mais
    novos. Um nome que aparece em pontos diferentes da mesma cidade num cadastro fica de fora."""
    for caminho in glob.glob(os.path.join(CACHE, 'eleitorado_local_votacao_*.zip')):
        if caminho not in _por_cadastro:
            _por_cadastro[caminho] = ler_coordenadas(caminho)
    juntas = {}
    for caminho in sorted(_por_cadastro, reverse=True):
        for chave, posicao in _por_cadastro[caminho].items():
            juntas.setdefault(chave, posicao)
    por_uf = {}
    for (uf, mun, nome), posicao in juntas.items():
        por_uf.setdefault(uf, {})[(mun, nome)] = posicao
    return por_uf


def ler_coordenadas(caminho):
    print(f'lendo coordenadas de {os.path.basename(caminho)}', flush=True)
    deste, ambiguos = {}, set()
    try:
        for cabecalho, leitor in tabelas(caminho):
            p = {nome: i for i, nome in enumerate(cabecalho)}
            if not {'SG_UF', 'CD_MUNICIPIO', 'NM_LOCAL_VOTACAO', 'NR_LATITUDE', 'NR_LONGITUDE'} <= p.keys():
                continue
            for r in leitor:
                lat, lon = coordenada(r[p['NR_LATITUDE']]), coordenada(r[p['NR_LONGITUDE']])
                if lat is None or lon is None or not no_brasil(lat, lon):
                    continue
                try:
                    chave = (r[p['SG_UF']], int(r[p['CD_MUNICIPIO']]), chave_local(r[p['NM_LOCAL_VOTACAO']]))
                except ValueError:
                    continue
                primeira = deste.setdefault(chave, (lat, lon))
                if abs(primeira[0] - lat) > 0.01 or abs(primeira[1] - lon) > 0.01:  # ~1 km
                    ambiguos.add(chave)
    except (zipfile.BadZipFile, OSError) as erro:
        print(f'aviso: {os.path.basename(caminho)} ignorado ({erro})', flush=True)
    return {chave: posicao for chave, posicao in deste.items() if chave not in ambiguos}


def completar_coordenadas(est, conhecidas):
    """Põe no mapa, pelas coordenadas de outros anos, as escolas da mesma cidade e com o mesmo nome."""
    for info in est.info:
        if info[5] is None and info[3]:
            posicao = conhecidas.get((info[0], chave_local(info[3])))
            if posicao:
                info[5], info[6] = posicao


def preencher_local(info, nome, endereco, bairro=None, latitude=None, longitude=None):
    if not info[3] and not vazio(nome):
        info[3] = nome.strip()
    if not info[4] and not vazio(endereco):
        info[4] = re.sub(r'\s+', ' ', endereco).strip()
    if not info[7] and not vazio(bairro):
        info[7] = limpar_bairro(bairro)
    if info[5] is None and latitude is not None and longitude is not None:
        info[5], info[6] = latitude, longitude


def limpar_bairro(texto):
    """Tira espaços repetidos e pontuação solta nas pontas ("- BAIRRO CENTRO" vira "BAIRRO CENTRO")."""
    return re.sub(r'\s+', ' ', texto).strip(' -–.,:;')


def chave_bairro(nome):
    """Grafias do mesmo bairro (acento, pontuação, "BAIRRO" na frente) dão a mesma chave."""
    chave = re.sub(r'[^A-Z0-9]+', ' ', normaliza(nome)).strip()
    return re.sub(r'^BAIRRO ', '', chave) or chave


def coordenada(texto):
    try:
        valor = float(texto.replace(',', '.'))
    except (AttributeError, ValueError):
        return None
    return None if valor == -1 else round(valor, 5)


def limpar_coordenadas(est):
    """Descarta coordenadas fora do Brasil (latitude e longitude trocadas, sinal errado etc.)."""
    if est.uf in ('ZZ', 'VT'):
        return
    for info in est.info:
        lat, lon = info[5], info[6]
        if lat is not None and not no_brasil(lat, lon):
            info[5] = info[6] = None


# ---------------------------------------------------------------- gravação


def slug(texto):
    return re.sub(r'[^a-z0-9]+', '-', normaliza(texto).lower()).strip('-')


def numero_compacto(n, saida):
    """Acrescenta o número inteiro n >= 0 a `saida` no formato do ALFABETO (1 caractere até 31)."""
    digitos = [n & 31]
    n >>= 5
    while n:
        digitos.append(n & 31)
        n >>= 5
    for d in reversed(digitos[1:]):
        saida.append(ALFABETO[32 + d])
    saida.append(ALFABETO[digitos[0]])


def votos_compactos(por_local):
    """{local: [(candidato, votos), ...]} -> texto. Para cada local, em ordem: distância até o local
    anterior, quantidade de pares e os pares (distância até o candidato anterior, votos)."""
    saida = []
    anterior = -1
    for l in sorted(por_local):
        pares = sorted(por_local[l])
        numero_compacto(l - anterior - 1, saida)
        numero_compacto(len(pares), saida)
        anterior = l
        c_anterior = -1
        for c, q in pares:
            numero_compacto(c - c_anterior - 1, saida)
            numero_compacto(q, saida)
            c_anterior = c
    return ''.join(saida)


def escrever_js(pasta, prefixo, nome, conteudo):
    caminho = os.path.join(pasta, nome + '.js')
    with open(caminho, 'w', encoding='utf-8') as arquivo:
        arquivo.write('// Gerado por scripts/gerar_dados.py - não edite à mão.\n')
        arquivo.write(f'window.registrarDados({json.dumps(prefixo + "/" + nome)}, ')
        json.dump(conteudo, arquivo, ensure_ascii=False, separators=(',', ':'))
        arquivo.write(');\n')
    return os.path.getsize(caminho)


def gravar(est):
    prefixo = f'{est.ano}/{est.uf}'
    pasta = os.path.join(DADOS, est.ano, est.uf)
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
    # bairros: na mesma cidade, grafias que só diferem em acento ou pontuação viram um só (vence a mais comum)
    grafias = {}
    for info in est.info:
        if info[7]:
            grafias.setdefault((info[0], chave_bairro(info[7])), collections.Counter())[info[7]] += 1
    nome_bairro = {grupo: contagem.most_common(1)[0][0] for grupo, contagem in grafias.items()}
    bairros = sorted(set(nome_bairro.values()), key=normaliza)
    idx_bairro = {nome: i for i, nome in enumerate(bairros)}

    def bairro_de(cd, bairro):
        return idx_bairro[nome_bairro[(cd, chave_bairro(bairro))]] if bairro else -1

    base = {
        'municipios': [est.municipios[cd] for cd in cods],
        'bairros': bairros,
        'locais': [[idx_mun[cd], str(zona), nome or f'LOCAL {numero}', endereco,
                    None if lat is None else round(lat, 4), None if lon is None else round(lon, 4),
                    bairro_de(cd, bairro)]
                   for cd, zona, numero, nome, endereco, lat, lon, bairro in (est.info[l] for l in ordem)],
    }
    bytes_base = escrever_js(pasta, prefixo, 'base', base)

    # Por turno e cargo. O mesmo cargo pode aparecer em mais de uma eleição do ano (as suplementares de
    # algumas cidades, feitas meses depois): a ordinária (ou, sem essa informação, a com mais votos) é a
    # principal e as outras vão para o fim.
    soma = {chave: sum(e['votos'].values()) for chave, e in est.eleicoes.items()}
    principal = {}
    for chave in sorted(est.eleicoes, key=lambda k: (k[1] != ORDINARIA, -soma[k])):
        principal.setdefault((chave[2], chave[3]), chave)
    cargos = []
    ids = set()
    for chave in sorted(est.eleicoes, key=lambda k: (principal[(k[2], k[3])] != k, int(k[2]), est.eleicoes[k]['cd_cargo'], -soma[k])):
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

        por_local = {}
        for k, q in e['votos'].items():
            por_local.setdefault(novo_local[k >> BITS], []).append((novo_v[k & MASCARA], q))

        rotulo = f"{e['cargo']} · {e['turno']}º turno"
        id_cargo = slug(f"{e['cargo']}-{e['turno']}t")
        if id_cargo in ids:  # mesmo cargo em outra eleição do ano (suplementar etc.)
            id_cargo = slug(f"{id_cargo}-{chave[1]}")
            rotulo = f"{rotulo} ({e['descricao']})"
        ids.add(id_cargo)
        candidatos = [[*e['votaveis'][v][:3], tipos[v]] for v in ordem_v]
        tamanho = escrever_js(pasta, prefixo, id_cargo,
                              {'rotulo': rotulo, 'candidatos': candidatos, 'votos': votos_compactos(por_local)})
        cargos.append({'id': id_cargo, 'rotulo': rotulo, 'bytes': tamanho})

    geracao = max(est.geracoes, key=lambda dh: data_hora(' '.join(dh)), default=('', ''))
    nome = ESTADOS.get(est.uf, est.uf)
    info = {
        'uf': est.uf,
        'nome': nome,
        'titulo': f"Eleições {'municipais' if municipal(est.ano) else 'gerais'} {est.ano} · {nome}",
        'gerado': f'{geracao[0]} {geracao[1][:5]}'.strip(),
        'cidades': len(cods),
        'locais': len(ordem),
        'base': {'bytes': bytes_base},
        'cargos': cargos,
    }
    with open(os.path.join(pasta, 'info.json'), 'w', encoding='utf-8') as arquivo:
        json.dump(info, arquivo, ensure_ascii=False, indent=1)
    sem_nome = sum(1 for i in est.info if not i[3])
    sem_mapa = sum(1 for i in est.info if i[5] is None)
    total = bytes_base + sum(c['bytes'] for c in cargos)
    avisos = [f'{sem_nome} sem nome' if sem_nome else '', f'{sem_mapa} sem coordenadas' if sem_mapa else '']
    avisos = ', '.join(a for a in avisos if a)
    return f"{est.ano} {est.uf}: {len(cods)} cidades, {len(ordem)} locais, {len(cargos)} cargos, {total / 1e6:.1f} MB" + \
        (f' (locais {avisos})' if avisos else '')


def atualizar_indice():
    """Reescreve data/eleicoes.js com todas as eleições (anos) e estados presentes em data/."""
    por_ano = {}
    for caminho in glob.glob(os.path.join(DADOS, '*', '*', 'info.json')):
        ano = os.path.basename(os.path.dirname(os.path.dirname(caminho)))
        if ano.isdigit():
            with open(caminho, encoding='utf-8') as arquivo:
                por_ano.setdefault(ano, []).append(json.load(arquivo))
    anos = []
    for ano in sorted(por_ano, reverse=True):
        estados = sorted(por_ano[ano], key=lambda e: (e['uf'] in ('ZZ', 'VT'), normaliza(e['nome'])))
        anos.append({'ano': ano, 'tipo': 'municipais' if municipal(ano) else 'gerais', 'estados': estados})
    indice = {'fonte': 'TSE · Dados abertos: votação por seção eleitoral', 'anos': anos}
    with open(os.path.join(DADOS, 'eleicoes.js'), 'w', encoding='utf-8') as arquivo:
        arquivo.write('// Gerado por scripts/gerar_dados.py - não edite à mão.\n')
        arquivo.write('window.ELEICOES = ')
        json.dump(indice, arquivo, ensure_ascii=False, separators=(',', ':'))
        arquivo.write(';\n')

    # formatos antigos: data/dados.js (um arquivo só) e data/estados.js + data/<UF>/ (sem o ano)
    for antigo in ('dados.js', 'estados.js'):
        if os.path.exists(os.path.join(DADOS, antigo)):
            os.remove(os.path.join(DADOS, antigo))
    for uf in ESTADOS:
        if os.path.isdir(os.path.join(DADOS, uf)):
            shutil.rmtree(os.path.join(DADOS, uf))
    return len(anos), sum(len(a['estados']) for a in anos)


# ---------------------------------------------------------------- execução


def processar_estado(ano, uf, arquivo, parcial, filtros, arquivo_locais, conhecidas):
    """Roda em um processo separado: lê o arquivo do estado, junta presidente e grava data/<ano>/<UF>/."""
    lidos = {}
    if arquivo:
        ler_votos(arquivo, lidos, filtros, {uf})
    est = lidos.get((ano, uf)) or Estado(uf, ano)
    if parcial:
        est.juntar(parcial)
    if not est.eleicoes:
        return f'{ano} {uf}: nenhum voto com esses filtros'
    if arquivo_locais:
        completar_locais(est, arquivo_locais)
    limpar_coordenadas(est)
    completar_coordenadas(est, conhecidas)
    return gravar(est)


def gerar_ano(ano, do_tse, parciais, ufs, filtros, a):
    """Gera data/<ano>/: baixa os arquivos do ano (se do_tse) e processa um estado por processo.

    `parciais` traz votos já lidos de arquivos com vários estados ({UF: Estado}). Devolve o nº de erros."""
    por_estado, baixados = {}, []
    if do_tse:
        alvo = [uf for uf in ufs or sorted(ESTADOS) if uf not in SEM_ARQUIVO_PROPRIO]
        with ThreadPoolExecutor(4) as fila:
            tarefas = {fila.submit(baixar, URL_VOTOS.format(ano=ano, uf=uf), False, a.usar_cache): uf for uf in alvo}
            if not a.sem_presidente and not municipal(ano):
                tarefas[fila.submit(baixar, URL_VOTOS.format(ano=ano, uf='BR'), False, a.usar_cache)] = 'BR'
            for tarefa in as_completed(tarefas):
                uf, caminho = tarefas[tarefa], tarefa.result()
                if caminho:
                    baixados.append(caminho)
                if uf != 'BR':
                    if caminho:
                        por_estado[uf] = caminho
                    else:
                        print(f'{ano} {uf}: o TSE não tem arquivo deste estado')
        # presidente: arquivo do país todo, lido uma vez e separado por estado
        for caminho in [c for c in baixados if c.endswith('_BR.zip')]:
            print(f'lendo {os.path.basename(caminho)}', flush=True)
            lidos = {}
            ler_votos(caminho, lidos, filtros, set(ufs) if ufs else None)
            for (_, uf), est in lidos.items():
                if uf in parciais:
                    parciais[uf].juntar(est)
                else:
                    parciais[uf] = est

    todos = sorted(set(por_estado) | set(parciais))
    if not todos:
        print(f'{ano}: nenhum voto encontrado')
        return 0

    # nome, endereço e coordenadas das escolas
    arquivo_locais = a.locais
    if not arquivo_locais and do_tse:
        arquivo_locais = baixar(URL_LOCAIS.format(ano=ano), obrigatorio=False, usar_cache=a.usar_cache)
        if not arquivo_locais:
            print(f'aviso: sem o arquivo de locais de votação de {ano}, o mapa fica sem as escolas')
    conhecidas = coordenadas_conhecidas()

    # um estado por processo, maiores primeiro
    ordem = sorted(todos, key=lambda uf: -os.path.getsize(por_estado[uf]) if uf in por_estado else 0)
    print(f'{ano}: processando {len(ordem)} estado(s) com {a.processos} processo(s)...', flush=True)
    erros = 0
    with ProcessPoolExecutor(max(1, a.processos)) as pool:
        tarefas = {pool.submit(processar_estado, ano, uf, por_estado.get(uf), parciais.pop(uf, None), filtros,
                               arquivo_locais, conhecidas.get(uf, {})): uf for uf in ordem}
        for tarefa in as_completed(tarefas):
            try:
                print(tarefa.result(), flush=True)
            except Exception as erro:
                erros += 1
                print(f'{ano} {tarefas[tarefa]}: ERRO {erro}', flush=True)

    if a.descartar_download and not erros:
        for caminho in baixados:
            apagar_baixado(caminho)
        print(f'{ano}: arquivos de votação baixados do TSE apagados de .cache-tse/', flush=True)
    return erros


def main():
    p = argparse.ArgumentParser(description='Gera a pasta data/ do painel a partir dos dados abertos do TSE.')
    p.add_argument('--ano', action='append', help='ano da eleição, ou intervalo como 2010-2026 (pode repetir); baixa os arquivos do TSE')
    p.add_argument('--uf', action='append', help='estado (pode repetir); sem --uf, gera todos')
    p.add_argument('--arquivo', action='append', default=[], help='votacao_secao_*.zip já baixado (pode repetir)')
    p.add_argument('--locais', help='eleitorado_local_votacao_*.zip já baixado')
    p.add_argument('--sem-presidente', action='store_true', help='não incluir os votos para presidente (arquivo BR)')
    p.add_argument('--usar-cache', action='store_true', help='usar os arquivos já baixados em .cache-tse/ sem procurar versão nova no TSE')
    p.add_argument('--descartar-download', action='store_true',
                   help='apagar de .cache-tse/ os arquivos de votação de cada ano depois de gerá-lo (economiza espaço; '
                        'os de locais ficam, para completar as coordenadas de outros anos)')
    p.add_argument('--cargo', action='append', help='manter só este cargo, ex.: Governador (pode repetir)')
    p.add_argument('--turno', action='append', help='manter só este turno: 1 ou 2 (pode repetir)')
    p.add_argument('--municipio', action='append', help='manter só esta cidade (pode repetir)')
    p.add_argument('--processos', type=int, default=min(4, os.cpu_count() or 1), help='estados processados ao mesmo tempo')
    a = p.parse_args()
    if not a.ano and not a.arquivo:
        p.error('informe --ano (para baixar do TSE) ou --arquivo')
    try:
        anos = expandir_anos(a.ano or [])
    except ValueError as erro:
        p.error(str(erro))
    if a.ano and not anos:
        p.error('não houve eleição nesse(s) ano(s): as eleições são nos anos pares')

    ufs = sorted({u.upper() for u in a.uf}) if a.uf else None
    for uf in ufs or []:
        if uf not in ESTADOS:
            p.error(f'estado desconhecido: {uf}')
    filtros = {
        'turno': set(a.turno or []),
        'cargo': {normaliza(c) for c in a.cargo or []},
        'municipio': {normaliza(m) for m in a.municipio or []},
    }

    erros = 0
    try:
        # arquivos passados com --arquivo: lidos uma vez e separados por ano e estado
        extras = {}
        for caminho in a.arquivo:
            print(f'lendo {os.path.basename(caminho)}', flush=True)
            ler_votos(caminho, extras, filtros, set(ufs) if ufs else None)
        for ano in sorted(set(anos) | {ano for ano, _ in extras}, reverse=True):
            parciais = {uf: est for (x, uf), est in extras.items() if x == ano}
            erros += gerar_ano(ano, ano in anos, parciais, ufs, filtros, a)
            atualizar_indice()  # cada ano já aparece no painel enquanto os outros são gerados
    except ErroTSE as erro:
        sys.exit(str(erro))

    n_anos, n_estados = atualizar_indice()
    print(f'ok: data/eleicoes.js com {n_anos} eleição(ões) e {n_estados} estado(s) no total' +
          (f' ({erros} com erro)' if erros else ''))


if __name__ == '__main__':
    main()
