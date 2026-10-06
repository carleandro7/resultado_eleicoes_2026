# Painel eleitoral

Painel estático (HTML + CSS + JS, sem banco de dados e sem servidor) com os votos
das eleições por **cidade**, **zona** e **escola/local de votação**, em qualquer
estado, com mapa, gráficos e tabela. Dá para escolher um ou mais candidatos (até 8)
e comparar os votos de cada um em cada local.

## Como abrir

Abra o `index.html` direto no navegador (duplo clique). Não precisa de servidor.

Os dados que acompanham o projeto são de **todas as eleições de 2010 a 2026**, com 1º
e 2º turno, todos os estados:

- **Gerais** (2010, 2014, 2018, 2022 e 2026): Presidente, Governador, Senador, Deputado
  Federal e Deputado Estadual/Distrital, mais o exterior (só Presidente). Em 2026, por
  enquanto só o 1º turno.
- **Municipais** (2012, 2016, 2020 e 2024): Prefeito e Vereador (o Distrito Federal não
  tem eleição municipal).

A eleição, o estado e o cargo escolhidos ficam no endereço da página
(ex.: `index.html#2024/PI/prefeito-1t`), então dá para recarregar ou mandar o link para
alguém já no recorte certo. Links antigos, sem o ano (`#PI/governador-1t`), abrem a
eleição mais recente.

## Atualizar ou gerar os dados

Os dados vêm dos arquivos abertos do TSE e são convertidos pelo script
`scripts/gerar_dados.py` (só usa Python 3, sem instalar nada):

```bash
# todos os estados + exterior (baixa ~1,4 GB do TSE na primeira vez)
python3 scripts/gerar_dados.py --ano 2026

# várias eleições de uma vez: um intervalo (só os anos pares) ou anos separados por vírgula
python3 scripts/gerar_dados.py --ano 2010-2024 --descartar-download
python3 scripts/gerar_dados.py --ano 2020,2024 --uf PI

# só alguns estados (os outros já gerados continuam no painel)
python3 scripts/gerar_dados.py --ano 2026 --uf PI --uf CE

# usar só o que já foi baixado, sem procurar versão nova no TSE (também funciona sem internet)
python3 scripts/gerar_dados.py --ano 2026 --usar-cache

# filtros opcionais
python3 scripts/gerar_dados.py --ano 2026 --uf SP --cargo Governador --cargo Presidente
python3 scripts/gerar_dados.py --ano 2024 --uf PI --municipio TERESINA
```

Os arquivos baixados ficam em `.cache-tse/` e só são baixados de novo quando o TSE
publica uma versão nova. **Depois do 2º turno**, é só rodar o mesmo comando: os
cargos "· 2º turno" aparecem sozinhos no painel.

As eleições de 2010 a 2024 somam cerca de 12 GB de arquivos do TSE. Com
`--descartar-download`, os arquivos de votação de cada ano são apagados de `.cache-tse/`
logo depois de gerado o ano (os de locais de votação ficam, porque são pequenos e servem
para completar as coordenadas dos outros anos). Gerar de novo um ano antigo baixa tudo
outra vez.

Cada eleição fica numa pasta `data/<ano>/`, e o script só reescreve os anos e estados
pedidos: gerar 2026 de novo não mexe nos anos anteriores.

Fontes usadas:

- **Votação por seção eleitoral** (`votacao_secao_<ano>_<UF>.zip`): votos de cada
  candidato em cada seção. O script soma as seções de cada local de votação.
- **Votação por seção, Brasil** (`votacao_secao_<ano>_BR.zip`): votos para presidente,
  separados por estado.
- **Eleitorado por local de votação** (`eleitorado_local_votacao_<ano>.zip`): bairro,
  latitude e longitude das escolas (para o filtro de bairro e o mapa), e nome e endereço
  quando o arquivo de votação vem sem eles (como nos primeiros dias depois da eleição).
  Grafias do mesmo bairro numa cidade (com e sem acento, "- BAIRRO CENTRO" e "CENTRO")
  são unificadas. Algumas escolas não têm coordenadas no cadastro do TSE e ficam fora
  do mapa; a página avisa quantas. Nas eleições até 2016 isso chega a quase 30% das
  escolas no cadastro do próprio ano. Por isso, quando a mesma escola (mesmo nome, na
  mesma cidade) aparece com coordenadas no cadastro de outro ano já baixado, o script
  usa essas (as do ano mais recente). Nomes que aparecem em dois pontos diferentes da
  mesma cidade não são usados.

## Como usar

- **Eleição**: o ano, com o tipo (gerais ou municipais). Ao trocar de eleição, o
  estado, a cidade, a zona, o bairro e a escola escolhidos continuam (pelo nome), para
  comparar o mesmo lugar em anos diferentes; o cargo também continua se existir na outra
  eleição (Governador de 2022 para 2018, por exemplo). Os números dos locais de votação
  mudam de um ano para outro, por isso a escola é procurada pelo nome; se ela não
  existir mais (ou tiver outro nome), o filtro de escola sai.
- **Estado / Cargo / Cidade / Zona / Bairro / Escola**: filtram tudo que aparece na
  página. Ao trocar de estado o cargo é mantido.
- **Prefeito e Vereador**: cada cidade tem os seus candidatos. Sem cidade escolhida,
  as listas mostram a cidade de cada candidato; com uma cidade escolhida, as cores fixas
  passam a ser as dos 8 mais votados dela.
- **Bairro**: fica liberado depois de escolher a cidade. Digite parte do nome e a lista
  vai filtrando, com quantos locais de votação cada bairro tem; a zona escolhida
  restringe os bairros oferecidos, e o bairro restringe a lista de escolas.
- **Escola**: digite parte do nome, do endereço, da cidade ou "zona 5" e a lista vai
  filtrando (sem diferença de acentos), dentro da cidade/zona escolhida. Escolhendo
  uma escola, o resumo mostra quem foi mais votado nela. Apagar o texto ou escolher
  "Todas as escolas" tira o filtro.
- **Candidatos**: digite parte do nome ou o número e marque um ou mais.
  - Nenhum selecionado: a tabela mostra o total de votos e o mais votado de cada local.
  - Um selecionado: votos, % dos válidos e colocação dele em cada local.
  - Dois ou mais: uma coluna por candidato, uma barra comparativa e quem está na
    frente em cada linha. O resumo mostra em quantas cidades, zonas e escolas
    cada um ficou na frente dos outros selecionados.
- **Ver por Cidade / Zona / Escola**: muda o agrupamento do mapa, do gráfico e da
  tabela. Clique no cabeçalho de uma coluna da tabela para ordenar.
- **Mapa**: um círculo por cidade, zona ou escola, do tamanho dos votos. A cor depende
  da seleção: sem candidato, mostra o mais votado em cada lugar; com um, o % dele
  (quanto mais forte a cor, maior o %); com dois ou mais, quem está na frente entre
  eles. Clicar numa cidade ou zona (no mapa ou no gráfico) mostra as escolas dela;
  clicar numa escola filtra só ela.
  A roda do mouse só dá zoom depois de clicar no mapa.
- **Gráfico**: as 15 cidades/zonas/escolas com mais votos. Sem candidato selecionado,
  mostra como os votos se dividiram entre os mais votados; com seleção, os votos dos
  selecionados.
- **Cores**: os 8 mais votados de cada cargo no estado têm cor fixa, a mesma no mapa,
  no gráfico e quando são selecionados.
- **Baixar CSV**: exporta a tabela atual (abre direto no Excel).
- **Baixar PDF**: relatório do recorte atual, com os filtros aplicados, os números
  principais, o resumo dos mais votados (ou dos candidatos escolhidos) e a tabela
  completa, com todas as linhas e não só as que aparecem na tela. Sai sempre no tema
  claro, deitado quando a tabela tem muitas colunas.
- **Tema**: a página abre no tema claro. O botão no canto do topo troca para o escuro
  (e de volta); a escolha fica guardada neste navegador.

Percentuais são sempre sobre os **votos válidos** (sem brancos e nulos). Para
Senador, quando há duas vagas, cada eleitor vota duas vezes, por isso o total de
votos passa do número de eleitores.

## Arquivos

```
index.html                    página
css/style.css                 visual (tema claro como padrão e escuro opcional)
js/app.js                     carregamento, filtros, cálculos, mapa, gráfico e tabela
vendor/leaflet/               biblioteca do mapa (Leaflet 1.9.4, licença BSD-2)
vendor/jspdf/                 geração do PDF (jsPDF 4.2.1 e jspdf-autotable 5.0.8, licença MIT),
                              carregada só quando alguém clica em "Baixar PDF"
data/eleicoes.js              lista das eleições (anos) e estados gerados
data/<ano>/<UF>/base.js       cidades, bairros e locais de votação do estado naquele ano
data/<ano>/<UF>/<cargo>.js    candidatos e votos por local (um arquivo por cargo e turno)
scripts/gerar_dados.py        conversor dos arquivos do TSE
```

A página só baixa a eleição, o estado e o cargo que estão sendo vistos, por isso
continua leve mesmo com todos os anos e estados. Os votos são gravados num texto
compacto (cada número em 1 a 3 caracteres, locais e candidatos como diferença para o
anterior), com cerca de 40% do tamanho em JSON comum, o que mantém a pasta `data/`
inteira abaixo do limite de 1 GB do GitHub Pages. Os maiores arquivos são os de
deputado e vereador em São Paulo.

O fundo do mapa (ruas, rios e nomes das cidades) vem dos mapas cinza da Esri
(`server.arcgisonline.com`), que não pedem chave de acesso. É o único recurso de fora
do projeto e precisa de internet; sem ele, os círculos continuam aparecendo.
