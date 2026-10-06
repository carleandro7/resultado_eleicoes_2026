# Painel eleitoral

Painel estático (HTML + CSS + JS, sem banco de dados e sem servidor) com os votos
da eleição por **cidade**, **zona** e **escola/local de votação**, em qualquer
estado, com mapa, gráficos e tabela. Dá para escolher um ou mais candidatos (até 8)
e comparar os votos de cada um em cada local.

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
- **Eleitorado por local de votação** (`eleitorado_local_votacao_<ano>.zip`): latitude
  e longitude das escolas (para o mapa), e nome e endereço quando o arquivo de votação
  vem sem eles (como nos primeiros dias depois da eleição). Algumas escolas não têm
  coordenadas no cadastro do TSE e ficam fora do mapa; a página avisa quantas.

## Como usar

- **Estado / Cargo / Cidade / Zona / Escola**: filtram tudo que aparece na página. Ao
  trocar de estado o cargo é mantido. A lista de escolas mostra as da cidade/zona
  escolhida; nos estados com mais de 1.500 escolas, escolha antes a cidade ou a zona.
  Escolhendo uma escola, o resumo mostra quem foi mais votado nela.
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

Percentuais são sempre sobre os **votos válidos** (sem brancos e nulos). Para
Senador, quando há duas vagas, cada eleitor vota duas vezes, por isso o total de
votos passa do número de eleitores.

## Arquivos

```
index.html              página
css/style.css           visual (tema claro e escuro automáticos)
js/app.js               carregamento, filtros, cálculos, mapa, gráfico e tabela
vendor/leaflet/         biblioteca do mapa (Leaflet 1.9.4, licença BSD-2)
data/estados.js         lista dos estados gerados
data/<UF>/base.js       cidades e locais de votação do estado
data/<UF>/<cargo>.js    candidatos e votos por local (um arquivo por cargo e turno)
scripts/gerar_dados.py  conversor dos arquivos do TSE
```

A página só baixa o estado e o cargo que estão sendo vistos, por isso continua leve
mesmo com todos os estados. Os maiores arquivos são os de deputado em São Paulo.

O fundo do mapa (ruas, rios e nomes das cidades) vem dos mapas cinza da Esri
(`server.arcgisonline.com`), que não pedem chave de acesso. É o único recurso de fora
do projeto e precisa de internet; sem ele, os círculos continuam aparecendo.
