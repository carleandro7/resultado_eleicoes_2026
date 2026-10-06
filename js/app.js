/* Painel eleitoral: lê os dados gerados por scripts/gerar_dados.py e desenha filtros, resumo e tabela.
 *
 * data/estados.js       window.ESTADOS_ELEICAO = { estados: [{ uf, nome, titulo, cargos: [{ id, rotulo, bytes }] }] }
 * data/<UF>/base.js     municipios: ["BOA VISTA", ...]
 *                       locais:     [[índice do município, "zona", "nome do local", "endereço"], ...]
 * data/<UF>/<cargo>.js  rotulo, candidatos: [[número, nome, UE, tipo], ...]
 *                       votos: [local, n, candidato, qtd, ... (n pares candidato/qtd), próximo local, n, ...]
 * Tipo do candidato: 0 = candidato, 1 = voto de legenda, 2 = branco/nulo.
 * Cada arquivo chama window.registrarDados(chave, dados); só o estado e o cargo vistos são baixados.
 */
(function () {
  'use strict';

  const INDICE = window.ESTADOS_ELEICAO;
  const MAX_SELECIONADOS = 8; // uma cor categórica por candidato; acima disso a comparação fica ilegível
  const PAGINA = 50;
  const LEGENDA = 1;
  const ESPECIAL = 2;
  const BRANCO = '95';

  const $ = (seletor) => document.querySelector(seletor);
  const nf = new Intl.NumberFormat('pt-BR');
  const pf = new Intl.NumberFormat('pt-BR', { style: 'percent', minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const ordenaTexto = new Intl.Collator('pt-BR', { numeric: true, sensitivity: 'base' }).compare;
  const num = (n) => nf.format(n);
  const pct = (parte, todo) => (todo > 0 ? pf.format(parte / todo) : '—');
  const plural = (n, um, varios) => `${num(n)} ${n === 1 ? um : varios}`;
  const semAcento = (s) => s.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  const h = (s) => String(s).replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  const tamanho = (bytes) => (bytes >= 1e6
    ? `${(bytes / 1e6).toLocaleString('pt-BR', { maximumFractionDigits: 1 })} MB`
    : `${Math.max(1, Math.round(bytes / 1e3))} KB`);

  if (!INDICE || !INDICE.estados || !INDICE.estados.length) {
    $('.pagina').innerHTML = `<div class="erro"><h1>Dados não encontrados</h1><p class="texto-sec">
      Gere a pasta <code>data/</code> com <code>python3 scripts/gerar_dados.py --ano 2026</code>
      e recarregue a página.</p></div>`;
    return;
  }

  // estado e cargo abertos no momento
  let INFO = null;       // entrada do estado em estados.js
  let CARGO_INFO = null; // entrada do cargo em INFO.cargos
  let D = null;          // base do estado: municipios e locais
  let CARGO = null;      // candidatos e votos do cargo
  let LOCAIS = [];
  let zonasDoMun = new Map();
  let todasZonas = [];

  const PARTICULAS = new Set(['de', 'da', 'do', 'das', 'dos', 'e']);
  function nomeProprio(texto) {
    return texto.toLowerCase().split(/\s+/)
      .map((p, i) => (i > 0 && PARTICULAS.has(p) ? p : p.charAt(0).toUpperCase() + p.slice(1)))
      .join(' ');
  }

  /* ---------- estado ---------- */

  const est = {
    mun: -1,          // índice do município; -1 = todos
    zona: '',         // '' = todas
    aba: 'municipio', // municipio | zona | local
    busca: '',
    sel: [],          // candidatos selecionados, na ordem em que foram escolhidos
    cor: new Map(),   // candidato -> posição da cor (0..7); a cor acompanha o candidato
    ord: null,        // { id da coluna, dir }
    limite: PAGINA,
  };
  let ultimo = null;      // último resultado de calcular()
  let tabelaAtual = null; // linhas e colunas exibidas (para o CSV)

  const eleicao = () => CARGO;
  const cands = () => eleicao().candidatos;
  // Os 8 mais votados do cargo no estado têm cor fixa (a lista de candidatos vem ordenada por votos),
  // usada no mapa e no gráfico; ao ser selecionado, o candidato fica com essa mesma cor se ela estiver livre.
  const corFixa = (c) => (c < MAX_SELECIONADOS && cands()[c][3] !== ESPECIAL ? c : -1);
  const corSlot = (slot) => `var(--s${slot + 1})`;
  const cor = (c) => corSlot(est.cor.get(c));
  const corNeutra = 'var(--neutro)';
  const corPadrao = (c) => (corFixa(c) >= 0 ? corSlot(corFixa(c)) : corNeutra);

  function nomeCand(c, curto) {
    const [, nome, , tipo] = cands()[c];
    if (tipo === LEGENDA) return 'Legenda ' + nomeProprio(nome);
    if (!curto || tipo === ESPECIAL) return nomeProprio(nome);
    const partes = nomeProprio(nome).split(' ').filter((p) => !PARTICULAS.has(p));
    return partes.length > 2 ? `${partes[0]} ${partes[partes.length - 1]}` : partes.join(' ');
  }

  function detalheCand(c) {
    const [numero, , ue, tipo] = cands()[c];
    if (tipo === ESPECIAL) return 'brancos e nulos';
    const varias = eleicao()._variasUes ??= new Set(cands().filter((x) => x[3] !== ESPECIAL).map((x) => x[2])).size > 1;
    return `nº ${numero}${varias && ue ? ' · ' + nomeProprio(ue) : ''}`;
  }

  function textoBuscaCand(c) {
    const el = eleicao();
    el._busca ??= el.candidatos.map(([numero, nome, ue]) => semAcento(`${numero} ${nome} ${ue}`));
    return el._busca[c];
  }

  /* ---------- cálculo ---------- */

  function novoGrupo(chave, n) {
    const g = { chave, total: 0, validos: 0, votos: new Map(), locais: new Set(), sel: new Float64Array(n) };
    if (est.aba === 'municipio') {
      g.nome = D.municipios[chave];
    } else if (est.aba === 'zona') {
      g.nome = 'Zona ' + chave;
    } else {
      const [mun, zona, nome, endereco] = LOCAIS[chave];
      Object.assign(g, { nome, endereco, mun, zona });
    }
    return g;
  }

  function somarDisputa(mapa, chave, i, q, n) {
    let v = mapa.get(chave);
    if (!v) mapa.set(chave, (v = new Float64Array(n)));
    v[i] += q;
  }

  // mais votado, colocação do candidato selecionado e quem lidera entre os selecionados
  function finalizar(g, C, n) {
    g.lider = -1;
    g.liderVotos = 0;
    for (const [c, q] of g.votos) {
      if (C[c][3] !== ESPECIAL && q > g.liderVotos) { g.lider = c; g.liderVotos = q; }
    }
    if (n === 1) {
      const alvo = g.sel[0];
      g.posicao = null;
      if (alvo > 0) {
        g.posicao = 1;
        for (const [c, q] of g.votos) if (C[c][3] !== ESPECIAL && q > alvo) g.posicao++;
      }
    }
    if (n >= 2) {
      const ordem = [...g.sel.keys()].sort((a, b) => g.sel[b] - g.sel[a]);
      g.somaSel = g.sel.reduce((a, b) => a + b, 0);
      g.margem = g.sel[ordem[0]] - g.sel[ordem[1]];
      g.frente = g.margem > 0 ? est.sel[ordem[0]] : -1; // -1 = empate
    }
  }

  function calcular() {
    const { candidatos: C, votos: V } = eleicao();
    const n = est.sel.length;
    const posSel = new Map(est.sel.map((c, i) => [c, i]));
    const grupos = new Map();
    const totCand = new Float64Array(C.length);
    const escopo = { total: 0, validos: 0, brancos: 0, nulos: 0, locais: new Set() };
    const disputa = n >= 2 ? { municipio: new Map(), zona: new Map(), local: new Map() } : null;

    for (let i = 0; i < V.length;) {
      const l = V[i];
      const fim = i + 2 + 2 * V[i + 1];
      const L = LOCAIS[l];
      if ((est.mun >= 0 && L[0] !== est.mun) || (est.zona && L[1] !== est.zona)) {
        i = fim;
        continue;
      }

      const chave = est.aba === 'municipio' ? L[0] : est.aba === 'zona' ? L[1] : l;
      let g = grupos.get(chave);
      if (!g) grupos.set(chave, (g = novoGrupo(chave, n)));
      g.locais.add(l);
      escopo.locais.add(l);

      for (i += 2; i < fim; i += 2) {
        const c = V[i], q = V[i + 1];
        g.total += q;
        escopo.total += q;
        totCand[c] += q;
        if (C[c][3] !== ESPECIAL) { g.validos += q; escopo.validos += q; }
        else if (C[c][0] === BRANCO) escopo.brancos += q;
        else escopo.nulos += q;
        g.votos.set(c, (g.votos.get(c) || 0) + q);

        const p = posSel.get(c);
        if (p !== undefined) {
          g.sel[p] += q;
          if (disputa) {
            somarDisputa(disputa.municipio, L[0], p, q, n);
            somarDisputa(disputa.zona, L[1], p, q, n);
            somarDisputa(disputa.local, l, p, q, n);
          }
        }
      }
    }

    const linhas = [...grupos.values()];
    for (const g of linhas) {
      finalizar(g, C, n);
      const muns = new Set(), zonas = new Set();
      let somaLat = 0, somaLon = 0, comPosicao = 0;
      for (const l of g.locais) {
        const L = LOCAIS[l];
        muns.add(L[0]);
        zonas.add(L[1]);
        if (L[4] != null) { somaLat += L[4]; somaLon += L[5]; comPosicao++; }
      }
      g.lat = comPosicao ? somaLat / comPosicao : null;
      g.lon = comPosicao ? somaLon / comPosicao : null;
      g.muns = [...muns].map((m) => D.municipios[m]).sort(ordenaTexto);
      g.nZonas = zonas.size;
      g.busca = semAcento([g.nome, g.endereco || '', ...g.muns].join(' '));
    }

    escopo.muns = new Set([...escopo.locais].map((l) => LOCAIS[l][0]));
    escopo.zonas = new Set([...escopo.locais].map((l) => LOCAIS[l][1]));

    // em quantas cidades / zonas / escolas cada selecionado fica na frente dos demais selecionados
    let vitorias = null;
    if (disputa) {
      vitorias = {};
      for (const nivel of Object.keys(disputa)) {
        const cont = new Array(n).fill(0);
        for (const v of disputa[nivel].values()) {
          let melhor = 0;
          for (let i = 1; i < n; i++) if (v[i] > v[melhor]) melhor = i;
          if (v[melhor] > 0 && v.filter((x) => x === v[melhor]).length === 1) cont[melhor]++;
        }
        vitorias[nivel] = { cont, disputados: disputa[nivel].size };
      }
    }

    return { C, linhas, totCand, escopo, vitorias };
  }

  /* ---------- filtros ---------- */

  const selEstado = $('#f-estado');
  const selEleicao = $('#f-eleicao');
  const selMun = $('#f-municipio');
  const selZona = $('#f-zona');

  function prepararEstado() {
    LOCAIS = D.locais;
    zonasDoMun = new Map();
    for (const [m, z] of LOCAIS) {
      if (!zonasDoMun.has(m)) zonasDoMun.set(m, new Set());
      zonasDoMun.get(m).add(z);
    }
    todasZonas = [...new Set(LOCAIS.map((L) => L[1]))].sort(ordenaTexto);

    document.title = `${INFO.nome} · Painel Eleitoral`;
    $('#titulo').textContent = INFO.titulo;
    $('#subtitulo').textContent = `${plural(INFO.cargos.length, 'cargo', 'cargos')} · ${plural(D.municipios.length, 'cidade', 'cidades')} · ${plural(LOCAIS.length, 'local de votação', 'locais de votação')}`;
    $('#rodape').textContent = `Fonte: ${INDICE.fonte || 'TSE'}${INFO.gerado ? ` · arquivo gerado pelo TSE em ${INFO.gerado}` : ''}`;
  }

  function montarFiltros() {
    selEstado.value = INFO.uf;
    selEleicao.innerHTML = INFO.cargos.map((c) => `<option value="${h(c.id)}">${h(c.rotulo)}</option>`).join('');
    selEleicao.value = CARGO_INFO.id;
    selMun.innerHTML = '<option value="-1">Todas as cidades</option>' +
      D.municipios.map((m, i) => `<option value="${i}">${h(m)}</option>`).join('');
    selMun.value = String(est.mun);
    montarZonas();
  }

  function montarZonas() {
    const zonas = est.mun >= 0 ? [...zonasDoMun.get(est.mun)].sort(ordenaTexto) : todasZonas;
    if (!zonas.includes(est.zona)) est.zona = '';
    selZona.innerHTML = '<option value="">Todas as zonas</option>' +
      zonas.map((z) => `<option value="${h(z)}">Zona ${h(z)}</option>`).join('');
    selZona.value = est.zona;
  }

  function descricaoEscopo() {
    const partes = [est.mun >= 0 ? D.municipios[est.mun] : 'Todas as cidades'];
    if (est.zona) partes.push('Zona ' + est.zona);
    return partes.join(' · ');
  }

  /* ---------- seletor de candidatos ---------- */

  const entrada = $('#f-candidato');
  const lista = $('#combo-lista');
  let visiveis = [];
  let ativo = -1;

  function alternar(c) {
    if (est.cor.has(c)) {
      est.cor.delete(c);
      est.sel = est.sel.filter((x) => x !== c);
    } else {
      if (est.sel.length >= MAX_SELECIONADOS) return;
      const usadas = new Set(est.cor.values());
      let slot = corFixa(c);
      if (slot < 0 || usadas.has(slot)) {
        // sem cor própria livre: pega a última livre, para não tomar a cor dos primeiros colocados
        slot = MAX_SELECIONADOS - 1;
        while (usadas.has(slot)) slot--;
      }
      est.cor.set(c, slot);
      est.sel.push(c);
    }
    est.limite = PAGINA;
    atualizar();
  }

  function limparSelecao() {
    est.sel = [];
    est.cor.clear();
    atualizar();
  }

  function abrirLista() {
    lista.hidden = false;
    entrada.setAttribute('aria-expanded', 'true');
    renderLista();
  }

  function fecharLista() {
    lista.hidden = true;
    entrada.setAttribute('aria-expanded', 'false');
    entrada.removeAttribute('aria-activedescendant');
    ativo = -1;
  }

  function renderLista() {
    if (lista.hidden || !ultimo) return;
    const { C, totCand } = ultimo;
    const termos = semAcento(entrada.value.trim()).split(/\s+/).filter(Boolean);
    const cheio = est.sel.length >= MAX_SELECIONADOS;

    const itens = [];
    for (let c = 0; c < C.length; c++) {
      if (!totCand[c] && !est.cor.has(c)) continue; // sem votos no recorte atual
      if (termos.length && !termos.every((t) => textoBuscaCand(c).includes(t))) continue;
      itens.push(c);
    }
    itens.sort((a, b) => (C[a][3] === ESPECIAL) - (C[b][3] === ESPECIAL) || totCand[b] - totCand[a]);
    visiveis = itens.slice(0, 100);
    if (ativo >= visiveis.length) ativo = visiveis.length - 1;

    let html = cheio ? `<li class="combo-aviso">Máximo de ${MAX_SELECIONADOS} candidatos na comparação.</li>` : '';
    html += visiveis.map((c, i) => {
      const escolhido = est.cor.has(c);
      return `<li id="opcao-${c}" class="opcao${i === ativo ? ' ativa' : ''}" role="option" data-c="${c}"
        aria-selected="${escolhido}"${cheio && !escolhido ? ' aria-disabled="true"' : ''}>
        <span class="opcao-marca"${escolhido ? ` style="background:${cor(c)}"` : ''}></span>
        <span class="opcao-nome">${h(nomeCand(c))}</span>
        <span class="opcao-votos">${num(totCand[c])}</span>
        <span class="opcao-info">${h(detalheCand(c))}</span>
      </li>`;
    }).join('');
    if (itens.length > visiveis.length) html += `<li class="combo-aviso">e mais ${num(itens.length - visiveis.length)}… digite para refinar.</li>`;
    if (!itens.length) html += '<li class="combo-aviso">Nenhum candidato encontrado.</li>';
    lista.innerHTML = html;

    if (ativo >= 0) {
      entrada.setAttribute('aria-activedescendant', 'opcao-' + visiveis[ativo]);
      document.getElementById('opcao-' + visiveis[ativo])?.scrollIntoView({ block: 'nearest' });
    } else {
      entrada.removeAttribute('aria-activedescendant');
    }
  }

  function renderChips() {
    const caixa = $('#selecionados');
    caixa.innerHTML = est.sel.map((c) => `
      <span class="chip">
        <span class="ponto" style="background:${cor(c)}"></span>
        ${h(nomeCand(c))} <small>${h(cands()[c][0])}</small>
        <button type="button" data-remover="${c}" aria-label="Remover ${h(nomeCand(c))}">×</button>
      </span>`).join('') +
      (est.sel.length ? '<button type="button" class="botao-texto" data-limpar>Limpar seleção</button>' : '');
    $('#cand-contagem').textContent = est.sel.length ? `(${est.sel.length} de ${MAX_SELECIONADOS})` : '';
  }

  /* ---------- indicadores e resumo ---------- */

  function renderKpis({ escopo }) {
    const kpi = (rotulo, valor, extra) =>
      `<div class="kpi"><span class="kpi-rotulo">${rotulo}</span><span class="kpi-valor">${valor}</span>${extra ? `<span class="kpi-extra">${extra}</span>` : ''}</div>`;
    $('#kpis').innerHTML = [
      kpi('Votos apurados', num(escopo.total), h(descricaoEscopo())),
      kpi('Votos válidos', num(escopo.validos), `${pct(escopo.validos, escopo.total)} do total`),
      kpi('Brancos e nulos', num(escopo.brancos + escopo.nulos), `${num(escopo.brancos)} brancos · ${num(escopo.nulos)} nulos`),
      kpi('Cidades', num(escopo.muns.size)),
      kpi('Zonas', num(escopo.zonas.size)),
      kpi('Escolas / locais', num(escopo.locais.size)),
    ].join('');
  }

  function linhaBarra(c, valor, max, validos, corBarra, detalhe, comoBotao) {
    const tag = comoBotao ? 'button' : 'div';
    return `<${tag} ${comoBotao ? `type="button" data-adicionar="${c}" title="Selecionar ${h(nomeCand(c))}"` : ''} class="barra-linha">
      <span class="barra-nome">${corBarra !== corNeutra ? `<span class="ponto" style="background:${corBarra}"></span>` : ''}
        <span>${h(nomeCand(c))}</span><small>${h(cands()[c][0])}</small></span>
      <span class="barra-trilho"><span class="barra-preenchida" style="width:${max ? (valor / max) * 100 : 0}%;background:${corBarra}"></span></span>
      <span class="barra-valor">${num(valor)}<small>${pct(valor, validos)}</small></span>
      ${detalhe ? `<span class="barra-detalhe">${detalhe}</span>` : ''}
    </${tag}>`;
  }

  function renderResumo({ C, totCand, escopo, vitorias }) {
    const alvo = $('#resumo');
    const n = est.sel.length;

    if (!n) {
      const top = [...C.keys()].filter((c) => C[c][3] !== ESPECIAL && totCand[c] > 0)
        .sort((a, b) => totCand[b] - totCand[a]).slice(0, 10);
      $('#resumo-titulo').textContent = 'Mais votados';
      $('#resumo-sub').textContent = `${descricaoEscopo()} · % sobre os votos válidos · clique num nome para selecionar`;
      alvo.innerHTML = top.length
        ? top.map((c) => linhaBarra(c, totCand[c], totCand[top[0]], escopo.validos, corPadrao(c), '', true)).join('')
        : '<p class="vazio">Sem votos para este recorte.</p>';
      return;
    }

    const ordem = [...est.sel].sort((a, b) => totCand[b] - totCand[a]);
    const max = totCand[ordem[0]];
    const niveis = [['municipio', 'cidade', 'cidades'], ['zona', 'zona', 'zonas'], ['local', 'escola', 'escolas']];

    $('#resumo-titulo').textContent = n === 1 ? 'Candidato selecionado' : 'Comparativo dos selecionados';
    $('#resumo-sub').textContent = `${descricaoEscopo()} · % sobre os votos válidos`;

    alvo.innerHTML = ordem.map((c) => {
      let detalhe = '';
      if (vitorias) {
        const i = est.sel.indexOf(c);
        detalhe = 'Na frente dos demais selecionados em ' + niveis
          .filter(([nivel]) => vitorias[nivel].disputados > 1)
          .map(([nivel, um, varios]) => `${num(vitorias[nivel].cont[i])} de ${plural(vitorias[nivel].disputados, um, varios)}`)
          .join(' · ');
        if (!niveis.some(([nivel]) => vitorias[nivel].disputados > 1)) detalhe = '';
      }
      return linhaBarra(c, totCand[c], max, escopo.validos, cor(c), detalhe, false);
    }).join('');

    const sugestoes = [...C.keys()]
      .filter((c) => C[c][3] !== ESPECIAL && totCand[c] > 0 && !est.cor.has(c))
      .sort((a, b) => totCand[b] - totCand[a]).slice(0, 5);
    if (sugestoes.length && n < MAX_SELECIONADOS) {
      alvo.innerHTML += `<p class="sugestoes"><span class="texto-sec">Comparar com:</span>${sugestoes.map((c) =>
        `<button type="button" class="botao botao-mini" data-adicionar="${c}">+ ${h(nomeCand(c, true))}</button>`).join('')}</p>`;
    }

    if (n >= 2) {
      const [a, b] = ordem;
      const dif = totCand[a] - totCand[b];
      const pp = escopo.validos ? ((dif / escopo.validos) * 100).toLocaleString('pt-BR', { maximumFractionDigits: 1 }) : '0';
      alvo.innerHTML += dif > 0
        ? `<p class="resumo-nota"><strong>${h(nomeCand(a))}</strong> tem ${num(dif)} votos a mais que ${h(nomeCand(b))} (${pp} pontos percentuais dos válidos).</p>`
        : `<p class="resumo-nota">${h(nomeCand(a))} e ${h(nomeCand(b))} estão empatados neste recorte.</p>`;
    }
  }

  /* ---------- tabela ---------- */

  const pctCsv = (parte, todo) => (todo > 0 ? ((parte / todo) * 100).toFixed(2).replace('.', ',') : '');

  // na linha de total a barra fica vazia: o total não está na mesma escala das linhas
  function miniBarra(valor, max, corBarra) {
    const largura = max > 0 ? Math.min(100, (valor / max) * 100) : 0;
    return `<div class="mini"><span class="mini-valor">${num(valor)}</span>
      <span class="mini-trilho"><span class="mini-preenchida" style="width:${largura.toFixed(2)}%;background:${corBarra}"></span></span></div>`;
  }

  function celulaLider(g) {
    if (g.lider < 0) return '—';
    return `${h(nomeCand(g.lider, true))} <small class="texto-mudo">${h(cands()[g.lider][0])}</small>`;
  }

  function colunaNome() {
    if (est.aba === 'municipio') {
      return {
        id: 'nome', rot: 'Cidade', texto: true, val: (g) => g.nome,
        cel: (g) => `${h(g.nome)}<small>${plural(g.nZonas, 'zona', 'zonas')} · ${plural(g.locais.size, 'local', 'locais')}</small>`,
        csv: [['Cidade', (g) => g.nome], ['Zonas', (g) => g.nZonas], ['Locais', (g) => g.locais.size]],
      };
    }
    if (est.aba === 'zona') {
      const cidades = (g) => (g.muns.length <= 2 ? g.muns.join(', ') : plural(g.muns.length, 'cidade', 'cidades'));
      return {
        id: 'nome', rot: 'Zona', texto: true, val: (g) => g.nome,
        cel: (g) => `${h(g.nome)}<small title="${h(g.muns.join(', '))}">${h(cidades(g))} · ${plural(g.locais.size, 'local', 'locais')}</small>`,
        csv: [['Zona', (g) => g.chave], ['Cidades', (g) => g.muns.join(', ')], ['Locais', (g) => g.locais.size]],
      };
    }
    return {
      id: 'nome', rot: 'Escola / local de votação', texto: true, val: (g) => g.nome,
      cel: (g) => {
        const sub = `${D.municipios[g.mun]} · Zona ${g.zona}${g.endereco ? ' · ' + g.endereco : ''}`;
        return `${h(g.nome)}<small title="${h(sub)}">${h(sub)}</small>`;
      },
      csv: [['Local', (g) => g.nome], ['Endereço', (g) => g.endereco], ['Cidade', (g) => D.municipios[g.mun]], ['Zona', (g) => g.zona]],
    };
  }

  function colunas(linhas) {
    const cols = [colunaNome()];
    const sel = est.sel;
    const maior = (fn) => linhas.reduce((m, g) => Math.max(m, fn(g)), 0);
    const validos = { id: 'validos', rot: 'Válidos', val: (g) => g.validos, cel: (g) => num(g.validos), csv: [['Votos válidos', (g) => g.validos]] };
    const lider = {
      id: 'lider', rot: 'Mais votado', texto: true,
      val: (g) => (g.lider >= 0 ? nomeCand(g.lider) : ''), cel: celulaLider,
      csv: [['Mais votado', (g) => (g.lider >= 0 ? nomeCand(g.lider) : '')], ['Votos do mais votado', (g) => g.liderVotos]],
    };

    if (!sel.length) {
      const max = maior((g) => g.total);
      cols.push(
        { id: 'total', rot: 'Votos', padrao: true, barra: true, val: (g) => g.total, cel: (g) => miniBarra(g.total, max, 'var(--neutro)'), csv: [['Votos', (g) => g.total]] },
        validos,
        lider,
        { id: 'liderPct', rot: '% do mais votado', val: (g) => (g.validos ? g.liderVotos / g.validos : 0), cel: (g) => pct(g.liderVotos, g.validos), csv: [['% do mais votado', (g) => pctCsv(g.liderVotos, g.validos)]] },
      );
      return cols;
    }

    if (sel.length === 1) {
      const c = sel[0];
      const max = maior((g) => g.sel[0]);
      const rotCsv = `${nomeCand(c)} (${cands()[c][0]})`;
      cols.push(
        {
          id: 'v' + c, rot: `<span class="ponto" style="background:${cor(c)}"></span>Votos`, titulo: nomeCand(c), padrao: true, barra: true,
          val: (g) => g.sel[0], cel: (g) => miniBarra(g.sel[0], max, cor(c)), csv: [[`${rotCsv} - votos`, (g) => g.sel[0]]],
        },
        { id: 'p' + c, rot: '% válidos', val: (g) => (g.validos ? g.sel[0] / g.validos : 0), cel: (g) => pct(g.sel[0], g.validos), csv: [[`${rotCsv} - % válidos`, (g) => pctCsv(g.sel[0], g.validos)]] },
        { id: 'pos', rot: 'Colocação', dirInicial: 'asc', val: (g) => g.posicao ?? Infinity, cel: (g) => (g.posicao ? `${g.posicao}º` : '—'), csv: [['Colocação', (g) => g.posicao ?? '']] },
        lider,
        validos,
      );
      return cols;
    }

    sel.forEach((c, i) => {
      const rotCsv = `${nomeCand(c)} (${cands()[c][0]})`;
      cols.push({
        id: 'v' + c, rot: `<span class="ponto" style="background:${cor(c)}"></span>${h(nomeCand(c, true))}`, titulo: `${nomeCand(c)} · ${detalheCand(c)}`,
        padrao: i === 0, val: (g) => g.sel[i],
        cel: (g) => `${num(g.sel[i])}<span class="cel-pct">${pct(g.sel[i], g.validos)}</span>`,
        csv: [[`${rotCsv} - votos`, (g) => g.sel[i]], [`${rotCsv} - % válidos`, (g) => pctCsv(g.sel[i], g.validos)]],
      });
    });
    cols.push(
      {
        id: 'comp', rot: 'Comparativo entre selecionados', semOrdem: true,
        cel: (g) => `<div class="pilha">${sel.map((c, i) => (g.sel[i] > 0
          ? `<span style="flex:${g.sel[i]} 1 0;background:${cor(c)}" data-dica="${h(`${nomeCand(c)}\n${num(g.sel[i])} votos · ${pct(g.sel[i], g.somaSel)} dos selecionados`)}"></span>`
          : '')).join('')}</div>`,
      },
      {
        id: 'margem', rot: 'Na frente', esquerda: true, val: (g) => g.margem,
        cel: (g) => (g.frente < 0
          ? '<span class="texto-mudo">empate</span>'
          : `<span class="cel-vencedor"><span class="ponto" style="background:${cor(g.frente)}"></span>${h(nomeCand(g.frente, true))} <small>+${num(g.margem)}</small></span>`),
        csv: [['Na frente', (g) => (g.frente < 0 ? 'empate' : nomeCand(g.frente))], ['Vantagem (votos)', (g) => g.margem]],
      },
      validos,
    );
    return cols;
  }

  function ordenar(linhas, cols) {
    let col = est.ord && cols.find((c) => c.id === est.ord.id && !c.semOrdem);
    let dir = est.ord?.dir;
    if (!col) {
      col = cols.find((c) => c.padrao);
      dir = 'desc';
    }
    const sinal = dir === 'asc' ? 1 : -1;
    linhas.sort((a, b) => {
      const va = col.val(a), vb = col.val(b);
      const r = typeof va === 'string' ? ordenaTexto(va, vb) : va - vb;
      return r * sinal || ordenaTexto(a.nome, b.nome);
    });
    return { col, dir };
  }

  function linhaTotal(linhas, C) {
    const n = est.sel.length;
    const g = { nome: 'Total', total: 0, validos: 0, votos: new Map(), locais: new Set(), sel: new Float64Array(n), muns: [] };
    for (const x of linhas) {
      g.total += x.total;
      g.validos += x.validos;
      for (let i = 0; i < n; i++) g.sel[i] += x.sel[i];
      for (const [c, q] of x.votos) g.votos.set(c, (g.votos.get(c) || 0) + q);
      for (const l of x.locais) g.locais.add(l);
    }
    finalizar(g, C, n);
    return g;
  }

  function renderTabela(r) {
    const termos = semAcento(est.busca.trim()).split(/\s+/).filter(Boolean);
    const linhas = termos.length ? r.linhas.filter((g) => termos.every((t) => g.busca.includes(t))) : [...r.linhas];
    const cols = colunas(linhas);
    const { col: colOrd, dir } = ordenar(linhas, cols);
    tabelaAtual = { linhas, cols };

    const tabela = $('#tabela');
    tabela.querySelector('thead').innerHTML = '<tr>' + cols.map((c) => {
      const numerica = !c.texto && !c.esquerda && c.id !== 'comp';
      const sort = c === colOrd ? ` aria-sort="${dir === 'asc' ? 'ascending' : 'descending'}"` : '';
      const titulo = c.titulo ? ` title="${h(c.titulo)}"` : '';
      const rot = c.semOrdem ? c.rot : `<button type="button" data-ordenar="${c.id}">${c.rot}</button>`;
      return `<th scope="col" class="${numerica ? 'num' : ''}"${sort}${titulo}>${rot}</th>`;
    }).join('') + '</tr>';

    const celulas = (g, total) => cols.map((c, i) => {
      if (i === 0) {
        return total
          ? `<td class="cel-nome">Total<small>${plural(linhas.length, 'linha', 'linhas')}</small></td>`
          : `<td class="cel-nome">${c.cel(g)}</td>`;
      }
      const numerica = !c.texto && !c.esquerda && c.id !== 'comp';
      const conteudo = total && c.barra ? miniBarra(c.val(g), 0, '') : c.cel(g);
      return `<td class="${numerica ? 'num' : ''}">${conteudo}</td>`;
    }).join('');

    const pagina = linhas.slice(0, est.limite);
    tabela.querySelector('tbody').innerHTML = pagina.length
      ? pagina.map((g) => `<tr>${celulas(g, false)}</tr>`).join('')
      : `<tr><td class="vazio" colspan="${cols.length}">Nada encontrado para este filtro.</td></tr>`;
    tabela.querySelector('tfoot').innerHTML = linhas.length > 1 ? `<tr>${celulas(linhaTotal(linhas, r.C), true)}</tr>` : '';

    const unidade = { municipio: ['cidade', 'cidades'], zona: ['zona', 'zonas'], local: ['local', 'locais'] }[est.aba];
    $('#tabela-titulo').textContent = `Tabela por ${UNIDADES[est.aba][0]}`;
    $('#contagem').textContent = `Exibindo ${num(pagina.length)} de ${plural(linhas.length, ...unidade)}`;
    $('#btn-mais').hidden = linhas.length <= est.limite;

    const notas = [
      'Selecione um ou mais candidatos para ver os votos de cada um por local e comparar.',
      'Percentual sobre os votos válidos de cada linha. Colocação entre todos os candidatos daquele local.',
      'Percentual sobre os votos válidos de cada linha. A barra divide só os votos dos candidatos selecionados.',
    ];
    $('#tabela-nota').textContent = notas[Math.min(est.sel.length, 2)];
  }

  function baixarCsv() {
    if (!tabelaAtual) return;
    const cabecalho = [];
    const valores = [];
    for (const col of tabelaAtual.cols) {
      for (const [rotulo, fn] of col.csv || []) { cabecalho.push(rotulo); valores.push(fn); }
    }
    const celula = (v) => {
      const s = v == null ? '' : String(v);
      return /[";\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    const texto = [cabecalho, ...tabelaAtual.linhas.map((g) => valores.map((fn) => fn(g)))]
      .map((linha) => linha.map(celula).join(';')).join('\r\n');
    const url = URL.createObjectURL(new Blob(['﻿' + texto], { type: 'text/csv;charset=utf-8' }));
    const link = document.createElement('a');
    const nomeArquivo = semAcento(`${INFO.uf} ${CARGO_INFO.rotulo} por ${est.aba}`).replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
    link.href = url;
    link.download = nomeArquivo + '.csv';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  /* ---------- carregamento ---------- */

  const arquivos = new Map();   // "PI/base" -> Promise com os dados
  const aguardando = new Map(); // "PI/base" -> resolve da Promise
  window.registrarDados = (chave, dados) => {
    const resolver = aguardando.get(chave);
    aguardando.delete(chave);
    if (resolver) resolver(dados);
  };

  // <script> em vez de fetch: funciona abrindo o index.html direto do disco (file://)
  function carregar(chave) {
    if (!arquivos.has(chave)) {
      arquivos.set(chave, new Promise((resolver, rejeitar) => {
        const falhar = () => {
          aguardando.delete(chave);
          arquivos.delete(chave);
          rejeitar(new Error(`não foi possível carregar data/${chave}.js`));
        };
        aguardando.set(chave, resolver);
        const tag = document.createElement('script');
        tag.src = `data/${chave}.js`;
        tag.onload = () => { tag.remove(); if (aguardando.has(chave)) falhar(); };
        tag.onerror = () => { tag.remove(); falhar(); };
        document.head.appendChild(tag);
      }));
    }
    return arquivos.get(chave);
  }

  const status = $('#status');
  function mostrarStatus(texto, erro) {
    status.textContent = texto;
    status.hidden = !texto;
    status.classList.toggle('status-erro', Boolean(erro));
  }

  let pedido = 0; // descarta respostas de trocas que já ficaram para trás

  async function abrir(uf, idCargo) {
    const meu = ++pedido;
    const info = INDICE.estados.find((e) => e.uf === uf) || INDICE.estados[0];
    const cargo = info.cargos.find((c) => c.id === idCargo)
      || info.cargos.find((c) => c.rotulo === CARGO_INFO?.rotulo) // mantém o cargo ao trocar de estado
      || info.cargos[0];
    const trocaEstado = info !== INFO;

    selEstado.value = info.uf;
    document.body.classList.add('carregando');
    mostrarStatus(`Carregando ${info.nome} · ${cargo.rotulo} (${tamanho(cargo.bytes + (trocaEstado ? info.base.bytes : 0))})…`);
    try {
      const [base, dados] = await Promise.all([carregar(`${info.uf}/base`), carregar(`${info.uf}/${cargo.id}`)]);
      if (meu !== pedido) return;
      if (trocaEstado) {
        INFO = info;
        D = base;
        est.mun = -1;
        est.zona = '';
        est.busca = '';
        $('#f-busca').value = '';
        prepararEstado();
        for (const chave of arquivos.keys()) if (!chave.startsWith(info.uf + '/')) arquivos.delete(chave); // libera memória
      }
      CARGO_INFO = cargo;
      CARGO = dados;
      est.sel = [];
      est.cor.clear();
      est.ord = null;
      est.limite = PAGINA;
      entrada.value = '';
      montarFiltros();
      lembrar();
      atualizar();
      mostrarStatus('');
    } catch (erro) {
      if (meu !== pedido) return;
      if (INFO) { selEstado.value = INFO.uf; selEleicao.value = CARGO_INFO.id; }
      mostrarStatus(`Erro: ${erro.message}`, true);
    } finally {
      if (meu === pedido) document.body.classList.remove('carregando');
    }
  }

  // estado e cargo ficam no endereço (#PI/governador-1t), para recarregar ou compartilhar o link
  function lembrar() {
    try {
      history.replaceState(null, '', `#${INFO.uf}/${CARGO_INFO.id}`);
      localStorage.setItem('painel-eleitoral-uf', INFO.uf);
    } catch (erro) { /* navegação privada ou file:// restrito: só não lembra */ }
  }

  function lerEndereco() {
    const [uf, cargo] = decodeURIComponent(location.hash.slice(1)).split('/');
    return [uf ? uf.toUpperCase() : '', cargo];
  }

  /* ---------- mapa e gráfico de barras ---------- */

  const UNIDADES = { municipio: ['cidade', 'cidades'], zona: ['zona', 'zonas'], local: ['escola', 'escolas'] };
  const TOP_BARRAS = 15;
  const OPACIDADES = [0.18, 0.36, 0.54, 0.72, 0.9]; // 5 faixas de % quando há um candidato selecionado
  const temaEscuro = window.matchMedia('(prefers-color-scheme: dark)');
  const ESRI = 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/';
  let mapa = null;
  let fundo = [];  // mapa base cinza + camada de nomes (Esri, sem chave de acesso)
  let pontos = null;
  let enquadramento = ''; // recorte (estado/cidade/zona) do último ajuste automático de zoom

  const itemLegenda = (corValor, rotulo, opacidade = 1, faixa = false) =>
    `<span class="legenda-item"><span class="${faixa ? 'legenda-faixa' : 'ponto'}" style="background:${corValor};opacity:${opacidade}"></span>${h(rotulo)}</span>`;

  function nomeComLocal(g) {
    if (est.aba === 'local') return `${g.nome}\n${D.municipios[g.mun]} · Zona ${g.zona}`;
    if (est.aba === 'zona') return `${g.nome} · ${g.muns.length <= 3 ? g.muns.join(', ') : plural(g.muns.length, 'cidade', 'cidades')}`;
    return g.nome;
  }

  function trocarFundo() {
    fundo.forEach((camada) => camada.remove());
    const tom = temaEscuro.matches ? 'Dark' : 'Light';
    const opcoes = { maxNativeZoom: 16, maxZoom: 18 };
    fundo = [
      L.tileLayer(`${ESRI}World_${tom}_Gray_Base/MapServer/tile/{z}/{y}/{x}`, {
        ...opcoes,
        attribution: 'Mapa base: <a href="https://www.esri.com">Esri</a>, HERE, Garmin, &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      }),
      L.tileLayer(`${ESRI}World_${tom}_Gray_Reference/MapServer/tile/{z}/{y}/{x}`, { ...opcoes, pane: 'rotulos' }),
    ];
    fundo.forEach((camada) => camada.addTo(mapa));
  }

  function iniciarMapa() {
    $('#mapa').innerHTML = '';
    mapa = L.map('mapa', { preferCanvas: true, zoomSnap: 0.5, scrollWheelZoom: false, maxZoom: 18 });
    // nomes das cidades por cima dos pontos, sem capturar o mouse
    mapa.createPane('rotulos');
    Object.assign(mapa.getPane('rotulos').style, { zIndex: 450, pointerEvents: 'none' });
    trocarFundo();
    pontos = L.featureGroup().addTo(mapa);
    // a roda do mouse só dá zoom depois de clicar no mapa, para não prender a rolagem da página
    mapa.on('click', () => mapa.scrollWheelZoom.enable());
    mapa.on('mouseout', () => mapa.scrollWheelZoom.disable());
    pontos.on('mouseover', (ev) => {
      ev.layer.setStyle({ weight: 2, color: ev.layer.contorno[1] });
      mostrarDica(ev.layer.dica(ev.layer.grupo));
    });
    pontos.on('mouseout', (ev) => {
      ev.layer.setStyle({ weight: 1, color: ev.layer.contorno[0] });
      dica.hidden = true;
    });
    pontos.on('click', (ev) => detalhar(ev.layer.grupo));
  }

  function mapaVazio(mensagem) {
    if (mapa) {
      mapa.remove();
      mapa = pontos = null;
      fundo = [];
      enquadramento = '';
    }
    $('#mapa').innerHTML = `<div class="mapa-vazio">${h(mensagem)}</div>`;
    $('#mapa-sub').textContent = '';
    $('#mapa-legenda').innerHTML = '';
    $('#mapa-nota').textContent = '';
  }

  // Cada ponto é uma cidade, zona ou escola (centro das escolas, para cidades e zonas).
  function renderMapa(r) {
    const n = est.sel.length;
    const [um, varios] = UNIDADES[est.aba];
    $('#mapa-titulo').textContent = `Mapa por ${um}`;
    const comPosicao = r.linhas.filter((g) => g.lat != null);
    if (!window.L) return mapaVazio('Não foi possível carregar a biblioteca do mapa.');
    if (!comPosicao.length) return mapaVazio('Os locais de votação deste recorte não têm coordenadas.');
    if (!mapa) iniciarMapa();

    let corDe, tamanhoDe, textoDe, legenda, sub;
    let opacidadeDe = () => 0.85;
    if (!n) {
      corDe = (g) => (g.lider >= 0 ? corPadrao(g.lider) : corNeutra);
      tamanhoDe = (g) => g.validos;
      textoDe = (g) => `${nomeComLocal(g)}\nMais votado: ${g.lider >= 0 ? `${nomeCand(g.lider)} (${pct(g.liderVotos, g.validos)})` : '—'}\n${num(g.validos)} votos válidos`;
      const lideres = [...new Set(comPosicao.map((g) => g.lider))];
      legenda = lideres.filter((c) => c >= 0 && corFixa(c) >= 0).sort((a, b) => a - b)
        .map((c) => itemLegenda(corPadrao(c), nomeCand(c, true))).join('') +
        (lideres.some((c) => c < 0 || corFixa(c) < 0) ? itemLegenda(corNeutra, 'Outros') : '');
      sub = `Cor: mais votado em cada ${um} · tamanho: votos válidos`;
    } else if (n === 1) {
      const c = est.sel[0];
      const parte = (g) => (g.validos ? g.sel[0] / g.validos : 0);
      const maior = comPosicao.reduce((m, g) => Math.max(m, parte(g)), 0);
      const passo = maior > 0.1 ? 0.05 : maior > 0.01 ? 0.01 : 0.001;
      const topo = Math.min(1, Math.max(passo, Math.ceil(maior / passo) * passo));
      const casas = topo >= 0.1 ? 0 : topo >= 0.01 ? 1 : 2;
      const fmt = (x) => (x * 100).toLocaleString('pt-BR', { maximumFractionDigits: casas });
      corDe = () => cor(c);
      opacidadeDe = (g) => (g.sel[0] > 0 ? OPACIDADES[Math.min(4, Math.floor((parte(g) / topo) * 5))] : 0.06);
      tamanhoDe = (g) => g.validos;
      textoDe = (g) => `${nomeComLocal(g)}\n${nomeCand(c)}: ${num(g.sel[0])} votos (${pct(g.sel[0], g.validos)})${g.posicao ? ` · ${g.posicao}º lugar` : ''}`;
      legenda = '<span class="legenda-titulo">% dos válidos</span>' + OPACIDADES
        .map((o, i) => itemLegenda(cor(c), `${fmt((topo * i) / 5)}–${fmt((topo * (i + 1)) / 5)}%`, o, true)).join('');
      sub = `Cor: % de ${nomeCand(c)} em cada ${um} · tamanho: votos válidos`;
    } else {
      corDe = (g) => (g.frente >= 0 ? cor(g.frente) : corNeutra);
      tamanhoDe = (g) => g.somaSel;
      textoDe = (g) => `${nomeComLocal(g)}\n` + est.sel.map((c, i) => `${nomeCand(c, true)}: ${num(g.sel[i])} (${pct(g.sel[i], g.validos)})`).join('\n');
      legenda = est.sel.map((c) => itemLegenda(cor(c), nomeCand(c, true))).join('') +
        (comPosicao.some((g) => g.frente < 0) ? itemLegenda(corNeutra, 'Empate ou sem votos') : '');
      sub = `Cor: quem está na frente entre os selecionados · tamanho: votos dos selecionados`;
    }

    // o canvas do mapa não entende var(--x): resolve cada cor uma vez por desenho
    const estilos = getComputedStyle(document.documentElement);
    const resolvidas = new Map();
    const corReal = (valor) => {
      if (!resolvidas.has(valor)) {
        const nome = /var\((--[\w-]+)\)/.exec(valor);
        resolvidas.set(valor, nome ? estilos.getPropertyValue(nome[1]).trim() : valor);
      }
      return resolvidas.get(valor);
    };
    const contorno = [corReal('var(--superficie)'), corReal('var(--texto)')];
    const maiorTamanho = comPosicao.reduce((m, g) => Math.max(m, tamanhoDe(g)), 0) || 1;
    const [rMin, rMax] = est.aba === 'local' ? [2.5, 9] : [4, 24];

    pontos.clearLayers();
    // os maiores primeiro, para os pequenos ficarem por cima
    for (const g of comPosicao.sort((a, b) => tamanhoDe(b) - tamanhoDe(a))) {
      const marca = L.circleMarker([g.lat, g.lon], {
        radius: rMin + (rMax - rMin) * Math.sqrt(tamanhoDe(g) / maiorTamanho),
        fillColor: corReal(corDe(g)),
        fillOpacity: opacidadeDe(g),
        color: contorno[0],
        weight: 1,
      });
      marca.grupo = g;
      marca.dica = textoDe;
      marca.contorno = contorno;
      pontos.addLayer(marca);
    }

    const recorte = `${INFO.uf}|${est.mun}|${est.zona}`;
    if (recorte !== enquadramento) {
      enquadramento = recorte;
      mapa.fitBounds(pontos.getBounds(), { padding: [20, 20], maxZoom: 15 });
    }

    const semPosicao = r.linhas.length - comPosicao.length;
    $('#mapa-sub').textContent = sub + (est.aba !== 'local' ? ` · clique numa ${um} para ver as escolas` : '');
    $('#mapa-legenda').innerHTML = legenda;
    $('#mapa-nota').textContent = semPosicao
      ? `${plural(semPosicao, um, varios)} sem coordenadas no cadastro do TSE ${semPosicao === 1 ? 'fica' : 'ficam'} fora do mapa.`
      : '';
  }

  temaEscuro.addEventListener?.('change', () => {
    if (!mapa) return;
    trocarFundo();
    if (ultimo) renderMapa(ultimo);
  });

  // Barras horizontais das cidades/zonas/escolas com mais votos, divididas por candidato.
  function renderBarras(r) {
    const n = est.sel.length;
    const [um, varios] = UNIDADES[est.aba];
    const Varios = varios.charAt(0).toUpperCase() + varios.slice(1);
    let valorDe, partesDe, legenda = '';
    let divisao = false; // true: todas as barras com o mesmo tamanho, mostrando a divisão dos votos
    if (!n) {
      // sem seleção: divisão dos válidos entre os mais votados do estado; quem tem menos de 1% na
      // linha entra em "Outros", senão vira um risco fino demais para ler
      const fixos = [...r.C.keys()].filter((c) => corFixa(c) >= 0);
      divisao = true;
      valorDe = (g) => g.validos;
      partesDe = (g) => {
        const partes = fixos.map((c) => [c, g.votos.get(c) || 0]).filter(([, q]) => q >= g.validos * 0.01);
        return [...partes, [-1, g.validos - partes.reduce((soma, [, q]) => soma + q, 0)]];
      };
      $('#barras-titulo').textContent = `Como votaram as ${TOP_BARRAS} ${varios} com mais votos`;
      $('#barras-sub').textContent = 'Divisão dos votos válidos entre os mais votados do estado';
      legenda = fixos.map((c) => itemLegenda(corPadrao(c), nomeCand(c, true))).join('') + itemLegenda(corNeutra, 'Outros');
    } else if (n === 1) {
      const c = est.sel[0];
      valorDe = (g) => g.sel[0];
      partesDe = (g) => [[c, g.sel[0]]];
      $('#barras-titulo').textContent = `${Varios} com mais votos de ${nomeCand(c, true)}`;
      $('#barras-sub').textContent = `As ${TOP_BARRAS} primeiras · % sobre os votos válidos de cada ${um}`;
    } else {
      valorDe = (g) => g.somaSel;
      partesDe = (g) => est.sel.map((c, i) => [c, g.sel[i]]);
      $('#barras-titulo').textContent = `${Varios} com mais votos dos selecionados`;
      $('#barras-sub').textContent = `As ${TOP_BARRAS} primeiras · soma dos votos dos selecionados`;
      legenda = est.sel.map((c) => itemLegenda(cor(c), nomeCand(c, true))).join('');
    }
    $('#barras-legenda').innerHTML = legenda;

    const top = r.linhas.filter((g) => valorDe(g) > 0).sort((a, b) => valorDe(b) - valorDe(a)).slice(0, TOP_BARRAS);
    const max = top.length ? valorDe(top[0]) : 0;
    const detalhavel = est.aba !== 'local';
    $('#grafico-barras').innerHTML = top.length ? top.map((g) => {
      const total = valorDe(g);
      const segmentos = partesDe(g).filter(([, q]) => q > 0).map(([c, q]) => {
        const nome = c < 0 ? 'Outros' : nomeCand(c);
        const corSegmento = c < 0 ? corNeutra : n ? cor(c) : corPadrao(c);
        return `<span style="flex:${q} 1 0;background:${corSegmento}" data-dica="${h(`${g.nome}\n${nome}: ${num(q)} votos (${pct(q, g.validos)} dos válidos)`)}"></span>`;
      }).join('');
      const nomeCompleto = est.aba === 'local' ? `${g.nome} · ${D.municipios[g.mun]}` : g.nome;
      const tag = detalhavel ? 'button' : 'div';
      return `<${tag} ${detalhavel ? `type="button" data-detalhar="${h(String(g.chave))}" title="Ver as escolas de ${h(g.nome)}"` : ''} class="barra-linha">
        <span class="barra-nome"><span title="${h(nomeCompleto)}">${h(g.nome)}</span></span>
        <span class="barra-trilho"><span class="pilha-abs" style="width:${divisao ? 100 : ((total / max) * 100).toFixed(2)}%">${segmentos}</span></span>
        <span class="barra-valor">${num(total)}${n === 1 ? `<small>${pct(total, g.validos)}</small>` : ''}</span>
      </${tag}>`;
    }).join('') : '<p class="vazio">Sem votos para este recorte.</p>';
  }

  function trocarAba(aba) {
    est.aba = aba;
    est.limite = PAGINA;
    document.querySelectorAll('[data-aba]').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.aba === aba)));
    atualizar();
  }

  // clique numa cidade ou zona (no mapa ou no gráfico): filtra por ela e mostra as escolas
  function detalhar(g) {
    if (!g || est.aba === 'local') return;
    dica.hidden = true;
    if (est.aba === 'municipio') {
      est.mun = g.chave;
      selMun.value = String(est.mun);
      montarZonas();
    } else {
      est.zona = g.chave;
      selZona.value = est.zona;
    }
    trocarAba('local');
  }

  /* ---------- ciclo de atualização ---------- */

  function atualizar() {
    ultimo = calcular();
    renderKpis(ultimo);
    renderResumo(ultimo);
    renderMapa(ultimo);
    renderBarras(ultimo);
    renderTabela(ultimo);
    renderChips();
    renderLista();
  }

  /* ---------- eventos ---------- */

  selEstado.addEventListener('change', () => abrir(selEstado.value));
  selEleicao.addEventListener('change', () => abrir(INFO.uf, selEleicao.value));
  window.addEventListener('hashchange', () => {
    const [uf, cargo] = lerEndereco();
    if (uf !== INFO?.uf || cargo !== CARGO_INFO?.id) abrir(uf, cargo);
  });

  selMun.addEventListener('change', () => {
    est.mun = Number(selMun.value);
    est.limite = PAGINA;
    montarZonas();
    atualizar();
  });

  selZona.addEventListener('change', () => {
    est.zona = selZona.value;
    est.limite = PAGINA;
    atualizar();
  });

  document.querySelectorAll('[data-aba]').forEach((botao) => {
    botao.addEventListener('click', () => trocarAba(botao.dataset.aba));
  });

  $('#grafico-barras').addEventListener('click', (ev) => {
    const botao = ev.target.closest('[data-detalhar]');
    if (botao && ultimo) detalhar(ultimo.linhas.find((g) => String(g.chave) === botao.dataset.detalhar));
  });

  $('#f-busca').addEventListener('input', (ev) => {
    est.busca = ev.target.value;
    est.limite = PAGINA;
    if (ultimo) renderTabela(ultimo);
  });

  $('#btn-mais').addEventListener('click', () => {
    est.limite += PAGINA;
    renderTabela(ultimo);
  });

  $('#btn-csv').addEventListener('click', baixarCsv);

  $('#tabela thead').addEventListener('click', (ev) => {
    const botao = ev.target.closest('[data-ordenar]');
    if (!botao) return;
    const id = botao.dataset.ordenar;
    const col = tabelaAtual.cols.find((c) => c.id === id);
    const atual = botao.closest('th').getAttribute('aria-sort');
    const dir = atual === 'descending' ? 'asc' : atual === 'ascending' ? 'desc' : col.dirInicial || (col.texto ? 'asc' : 'desc');
    est.ord = { id, dir };
    renderTabela(ultimo);
  });

  $('#resumo').addEventListener('click', (ev) => {
    const botao = ev.target.closest('[data-adicionar]');
    if (botao) alternar(Number(botao.dataset.adicionar));
  });

  $('#selecionados').addEventListener('click', (ev) => {
    const remover = ev.target.closest('[data-remover]');
    if (remover) alternar(Number(remover.dataset.remover));
    else if (ev.target.closest('[data-limpar]')) limparSelecao();
  });

  entrada.addEventListener('focus', abrirLista);
  entrada.addEventListener('click', abrirLista);
  entrada.addEventListener('input', () => {
    ativo = entrada.value.trim() ? 0 : -1;
    if (lista.hidden) abrirLista(); else renderLista();
  });
  entrada.addEventListener('keydown', (ev) => {
    if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
      ev.preventDefault();
      if (lista.hidden) return abrirLista();
      const passo = ev.key === 'ArrowDown' ? 1 : -1;
      ativo = Math.max(0, Math.min(visiveis.length - 1, ativo + passo));
      renderLista();
    } else if (ev.key === 'Enter') {
      ev.preventDefault();
      if (ativo >= 0 && visiveis[ativo] !== undefined) {
        const escolhido = visiveis[ativo];
        entrada.value = '';
        ativo = -1;
        alternar(escolhido);
      }
    } else if (ev.key === 'Escape') {
      fecharLista();
    } else if (ev.key === 'Backspace' && !entrada.value && est.sel.length) {
      alternar(est.sel[est.sel.length - 1]);
    }
  });
  lista.addEventListener('mousedown', (ev) => {
    ev.preventDefault(); // mantém o foco no campo para poder marcar vários seguidos
    const item = ev.target.closest('[data-c]');
    if (item && item.getAttribute('aria-disabled') !== 'true') alternar(Number(item.dataset.c));
  });
  document.addEventListener('mousedown', (ev) => {
    // composedPath: o item clicado já foi trocado pelo re-render, mas o caminho original continua válido
    if (!ev.composedPath().some((no) => no.classList?.contains('combo'))) fecharLista();
  });
  entrada.addEventListener('blur', () => setTimeout(() => {
    if (document.activeElement !== entrada) fecharLista();
  }, 0));

  // dica ao passar o mouse nas barras e nos pontos do mapa
  const dica = $('#dica');
  function mostrarDica(texto) {
    dica.textContent = texto;
    dica.style.whiteSpace = 'pre-line';
    dica.hidden = false;
  }
  document.addEventListener('mouseover', (ev) => {
    const alvo = ev.target.closest('[data-dica]');
    if (alvo) mostrarDica(alvo.dataset.dica);
    else dica.hidden = true;
  });
  document.addEventListener('mousemove', (ev) => {
    if (dica.hidden) return;
    const margem = 14;
    const largura = dica.offsetWidth, altura = dica.offsetHeight;
    let x = ev.clientX + margem, y = ev.clientY + margem;
    if (x + largura > window.innerWidth - 8) x = ev.clientX - largura - margem;
    if (y + altura > window.innerHeight - 8) y = ev.clientY - altura - margem;
    dica.style.left = x + 'px';
    dica.style.top = y + 'px';
  });

  /* ---------- início ---------- */

  selEstado.innerHTML = INDICE.estados.map((e) => `<option value="${h(e.uf)}">${h(e.nome)}</option>`).join('');
  let ufSalva = '';
  try { ufSalva = localStorage.getItem('painel-eleitoral-uf') || ''; } catch (erro) { /* sem localStorage */ }
  const [ufLink, cargoLink] = lerEndereco();
  const ufInicial = [ufLink, ufSalva].find((uf) => INDICE.estados.some((e) => e.uf === uf)) || INDICE.estados[0].uf;
  abrir(ufInicial, cargoLink);
})();
