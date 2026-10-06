# Painel eleitoral

Painel estático (HTML + CSS + JS, sem banco de dados e sem servidor) com os votos
da eleição por **cidade**, **zona** e **escola/local de votação**, em qualquer
estado. Dá para escolher um ou mais candidatos (até 8) e comparar os votos de cada
um em cada local.

## Como abrir

Abra o `index.html` direto no navegador (duplo clique). Não precisa de servidor.

Os dados que acompanham o projeto são das **eleições de 2026 (1º turno)**, todos os
estados e o exterior: Presidente, Governador, Senador, Deputado Federal e Deputado
Estadual/Distrital.

O estado e o cargo escolhidos ficam no endereço da página (ex.: `index.html#PI/governador-1t`),
então dá para recarregar ou mandar o link para alguém já no recorte certo.

## Atualizar ou gerar os dados

Os dados vêm dos arquivos abertos do TSE e são convertidos pelo script
`scripts/gerar_dados.py` (só usa Python 3, sem instalar nada):

```bash
# todos os estados + exterior (baixa ~1,4 GB do TSE na primeira vez)
python3 scripts/gerar_dados.py --ano 2026

# só alguns estados (os outros já gerados continuam no painel)
python3 scripts/gerar_dados.py --ano 2026 --uf PI --uf CE

# filtros opcionais
python3 scripts/gerar_dados.py --ano 2026 --uf SP --cargo Governador --cargo Presidente
python3 scripts/gerar_dados.py --ano 2024 --uf PI --municipio TERESINA
```

Os arquivos baixados ficam em `.cache-tse/` e só são baixados de novo quando o TSE
publica uma versão nova. **Depois do 2º turno**, é só rodar o mesmo comando: os
cargos "· 2º turno" aparecem sozinhos no painel.

Fontes usadas:

- **Votação por seção eleitoral** (`votacao_secao_<ano>_<UF>.zip`): votos de cada
  candidato em cada seção. O script soma as seções de cada local de votação.
- **Votação por seção, Brasil** (`votacao_secao_<ano>_BR.zip`): votos para presidente,
  separados por estado.
- **Eleitorado por local de votação** (`eleitorado_local_votacao_<ano>.zip`): nome e
  endereço das escolas quando o arquivo de votação vem sem eles (como nos primeiros
  dias depois da eleição).

## Como usar

- **Estado / Cargo / Cidade / Zona**: filtram tudo que aparece na página. Ao trocar de
  estado o cargo é mantido.
- **Candidatos**: digite parte do nome ou o número e marque um ou mais.
  - Nenhum selecionado: a tabela mostra o total de votos e o mais votado de cada local.
  - Um selecionado: votos, % dos válidos e colocação dele em cada local.
  - Dois ou mais: uma coluna por candidato, uma barra comparativa e quem está na
    frente em cada linha. O resumo mostra em quantas cidades, zonas e escolas
    cada um ficou na frente dos outros selecionados.
- **Por cidade / Por zona / Por escola**: muda o agrupamento da tabela. Clique no
  cabeçalho de uma coluna para ordenar.
- **Baixar CSV**: exporta a tabela atual (abre direto no Excel).

Percentuais são sempre sobre os **votos válidos** (sem brancos e nulos). Para
Senador, quando há duas vagas, cada eleitor vota duas vezes, por isso o total de
votos passa do número de eleitores.

## Arquivos

```
index.html              página
css/style.css           visual (tema claro e escuro automáticos)
js/app.js               carregamento, filtros, cálculos e tabela
data/estados.js         lista dos estados gerados
data/<UF>/base.js       cidades e locais de votação do estado
data/<UF>/<cargo>.js    candidatos e votos por local (um arquivo por cargo e turno)
scripts/gerar_dados.py  conversor dos arquivos do TSE
```

A página só baixa o estado e o cargo que estão sendo vistos, por isso continua leve
mesmo com todos os estados. Os maiores arquivos são os de deputado em São Paulo.
