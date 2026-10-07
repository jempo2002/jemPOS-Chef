/* Domicilios: lista de los que estan en curso (por despachar, en camino,
 * entregados sin liquidar), despacho con domiciliario y hoja para el
 * domiciliario con lo que debe cobrar y el vuelto. Se refresca cada 10 s.
 * La plata se recibe en Caja (recaudo.js). */
(function () {
  'use strict';

  var raiz = document.getElementById('domicilios');
  var puedeCobrar = raiz.dataset.cobrar === '1';
  var CLAVE_FILTRO = 'chef_filtro_domicilios_v1';
  var ESTADOS = { por_despachar: 'Por despachar', despachado: 'En camino', entregado: 'Entregado' };
  var METODOS = { 'Efectivo': 'Efectivo', 'Nequi/Daviplata': 'Nequi', 'Tarjeta': 'Tarjeta', 'Mixto': 'Mixto' };
  var datos = { domicilios: [], domiciliarios: [] };
  var filtro = Chef.leer(CLAVE_FILTRO, 'todos');
  var despachando = null;

  function $(id) { return document.getElementById(id); }
  function el(tag, clase, texto) {
    var n = document.createElement(tag);
    if (clase) n.className = clase;
    if (texto !== undefined && texto !== null) n.textContent = texto;
    return n;
  }
  function boton(texto, clase, accion) {
    var b = el('button', clase, texto);
    b.type = 'button';
    b.addEventListener('click', accion);
    return b;
  }
  function numero(v) { return Number(String(v || '').replace(/\D/g, '')) || 0; }

  /* --- lista ----------------------------------------------------------- */

  function pintar() {
    var cont = $('lista');
    cont.textContent = '';
    var n = { todos: 0, por_despachar: 0, despachado: 0, entregado: 0 };
    datos.domicilios.forEach(function (d) { n.todos += 1; n[d.estado] += 1; });
    Object.keys(n).forEach(function (k) { document.querySelector('[data-n="' + k + '"]').textContent = n[k]; });
    document.querySelectorAll('#filtros [data-filtro]').forEach(function (b) {
      b.classList.toggle('pestana--activa', b.dataset.filtro === filtro);
    });
    var visibles = datos.domicilios.filter(function (d) { return filtro === 'todos' || d.estado === filtro; });
    $('sin-domicilios').hidden = visibles.length > 0;
    visibles.forEach(function (d) { cont.appendChild(tarjeta(d)); });
    $('resumen-domicilios').textContent = n.por_despachar + ' por despachar · ' + n.despachado + ' en camino' +
      (navigator.onLine ? '' : ' · sin conexión, datos de antes');
    pintarDomiciliarios();
  }

  function tarjeta(d) {
    var t = el('article', 'mesa domicilio domicilio--' + d.estado);
    var cab = el('div', 'mesa__cab');
    cab.appendChild(el('strong', 'mesa__nombre', '#' + d.numero + (d.cliente ? ' · ' + d.cliente : '')));
    cab.appendChild(el('span', 'mesa__estado', ESTADOS[d.estado]));
    t.appendChild(cab);
    t.appendChild(el('span', 'domicilio__direccion', d.direccion));
    var pie = el('div', 'mesa__pie');
    pie.appendChild(el('span', 'mesa__total', Chef.pesos(d.total)));
    pie.appendChild(el('small', 'mesa__meta', d.pagado ? 'Pagado' : (METODOS[d.metodo_pago] || d.metodo_pago)));
    t.appendChild(pie);
    if (d.estado === 'por_despachar') {
      t.appendChild(el('small', 'mesa__meta', 'Pedido hace ' + d.minutos + ' min'));
      if (d.sin_enviar) t.appendChild(el('span', 'insignia', d.sin_enviar + ' sin enviar a cocina'));
      else if (d.listos) t.appendChild(el('span', 'insignia insignia--ok', 'Listo para salir'));
      else if (d.en_cocina) t.appendChild(el('span', 'insignia insignia--cocina', 'En cocina'));
    } else {
      t.appendChild(el('small', 'mesa__meta', d.domiciliario + ' · salió ' + d.despachado_a_las +
        (d.minutos_en_calle !== null ? ' (hace ' + d.minutos_en_calle + ' min)' : '')));
      if (d.efectivo) t.appendChild(el('span', 'insignia', 'Cobra ' + Chef.pesos(d.efectivo) + ' en efectivo'));
    }
    var acciones = el('div', 'domicilio__acciones');
    var ver = el('a', 'btn', d.estado === 'por_despachar' ? 'Ver pedido' : 'Ver');
    ver.href = '/domicilio/' + d.uuid;
    acciones.appendChild(ver);
    if (d.estado === 'por_despachar') {
      var b = boton('Despachar', 'btn btn--primario', function () { abrirDespacho(d); });
      if (d.sin_enviar) { b.disabled = true; b.title = 'Primero envía el pedido a cocina'; }
      acciones.appendChild(b);
    } else {
      if (d.estado === 'despachado') acciones.appendChild(boton('Ya lo entregó', 'btn btn--primario', function () { entregado(d); }));
      acciones.appendChild(boton('Hoja', 'btn', function () { imprimirHoja(hojaDe(d)); }));
    }
    t.appendChild(acciones);
    return t;
  }

  function pintarDomiciliarios() {
    var ul = $('domiciliarios');
    ul.textContent = '';
    $('sin-domiciliarios').hidden = datos.domiciliarios.length > 0;
    datos.domiciliarios.forEach(function (m) {
      var li = el('li', 'domiciliario-item');
      li.appendChild(el('strong', '', m.nombre));
      if (m.telefono) {
        var tel = el('a', 'muted', m.telefono);
        tel.href = 'tel:' + m.telefono;
        li.appendChild(tel);
      }
      var calle = datos.domicilios.filter(function (d) { return d.id_domiciliario === m.id_domiciliario && d.estado !== 'por_despachar'; }).length;
      li.appendChild(el('span', 'chip' + (calle ? '' : ' chip--ok'), calle ? calle + ' en la calle' : 'Disponible'));
      if (puedeCobrar) {
        li.appendChild(boton('Editar', 'btn-link', function () { editarDomiciliario(m); }));
        li.appendChild(boton('Quitar', 'btn-link btn-link--peligro', function () { quitarDomiciliario(m); }));
      }
      ul.appendChild(li);
    });
  }

  function cargar() {
    return Chef.api('GET', '/api/domicilios').then(function (r) {
      if (!r.ok) { Chef.mostrar(r.msg || 'No se pudieron cargar los domicilios.', true); return; }
      datos = r;
      Chef.guardar('chef_domicilios_v1', datos);
      pintar();
    }, function () {
      datos = Chef.leer('chef_domicilios_v1', datos);
      pintar();
    });
  }

  /* --- despacho -------------------------------------------------------- */

  function abrirDespacho(d) {
    if (!datos.domiciliarios.length) {
      Chef.mostrar(puedeCobrar ? 'Agrega un domiciliario abajo para poder despachar.' : 'No hay domiciliarios. Pídele al cajero que los agregue.', true);
      return;
    }
    despachando = d;
    $('desp-titulo').textContent = '#' + d.numero + (d.cliente ? ' · ' + d.cliente : '');
    $('desp-direccion').textContent = d.direccion + (d.telefono ? ' · Tel. ' + d.telefono : '');
    var sel = $('desp-domiciliario');
    sel.textContent = '';
    var vacia = el('option', '', 'Elige el domiciliario');
    vacia.value = '';
    sel.appendChild(vacia);
    datos.domiciliarios.forEach(function (m) {
      var calle = datos.domicilios.filter(function (x) { return x.id_domiciliario === m.id_domiciliario && x.estado !== 'por_despachar'; }).length;
      var o = el('option', '', m.nombre + (calle ? ' (lleva ' + calle + ')' : ' (disponible)'));
      o.value = m.id_domiciliario;
      sel.appendChild(o);
    });
    if (datos.domiciliarios.length === 1) sel.value = datos.domiciliarios[0].id_domiciliario;
    $('desp-pagado').hidden = !d.pagado;
    $('desp-cobro').hidden = d.pagado;
    var metodos = { 'Efectivo': 'efectivo', 'Nequi/Daviplata': 'nequi', 'Tarjeta': 'tarjeta', 'Mixto': 'mixto' };
    $('desp-metodo').value = metodos[d.metodo_pago] || 'efectivo';
    $('desp-mixto').value = '';
    $('desp-paga-con').value = '';
    pintarBilletes();
    recalcular();
    $('dlg-despachar').showModal();
  }

  /* Botones rapidos con los billetes que alcanzan para pagar. */
  function pintarBilletes() {
    var cont = $('desp-billetes');
    cont.textContent = '';
    var aPagar = efectivoACobrar();
    cont.appendChild(boton('Exacto', 'btn btn--mini', function () { $('desp-paga-con').value = aPagar; recalcular(); }));
    [20000, 50000, 100000].forEach(function (b) {
      if (b <= aPagar) return;
      cont.appendChild(boton(Chef.pesos(b), 'btn btn--mini', function () { $('desp-paga-con').value = b; recalcular(); }));
    });
  }

  function efectivoACobrar() {
    if (!despachando || despachando.pagado) return 0;
    var metodo = $('desp-metodo').value;
    if (metodo === 'efectivo') return despachando.total;
    if (metodo === 'mixto') return Math.min(numero($('desp-mixto').value), despachando.total);
    return 0;
  }

  function recalcular() {
    var d = despachando;
    var metodo = $('desp-metodo').value;
    $('desp-bloque-mixto').hidden = metodo !== 'mixto';
    $('desp-mixto').required = metodo === 'mixto' && !d.pagado;
    var efectivo = efectivoACobrar();
    $('desp-bloque-paga').hidden = d.pagado || !efectivo;
    var texto;
    if (d.pagado) texto = '$0 (pagado)';
    else if (metodo === 'efectivo') texto = Chef.pesos(d.total) + ' en efectivo';
    else if (metodo === 'mixto') texto = Chef.pesos(efectivo) + ' efectivo + ' + Chef.pesos(d.total - efectivo) + ' transferencia';
    else texto = Chef.pesos(d.total) + (metodo === 'tarjeta' ? ' con datáfono' : ' por transferencia');
    $('desp-cobrar').textContent = texto;
    var paga = numero($('desp-paga-con').value);
    var vuelto = paga && efectivo ? paga - efectivo : 0;
    $('desp-fila-vuelto').hidden = !(paga && efectivo);
    $('desp-vuelto').textContent = vuelto >= 0 ? Chef.pesos(vuelto) : 'No alcanza';
  }

  function despachar() {
    var d = despachando;
    var cuerpo = { id_domiciliario: $('desp-domiciliario').value };
    if (!d.pagado) {
      cuerpo.metodo = $('desp-metodo').value;
      if (cuerpo.metodo === 'mixto') cuerpo.monto_efectivo = numero($('desp-mixto').value);
      cuerpo.paga_con = numero($('desp-paga-con').value) || null;
    }
    var imprimirla = $('desp-imprimir').checked;
    Chef.api('POST', '/api/domicilios/' + d.id_pedido + '/despachar', cuerpo).then(function (r) {
      if (!r.ok) { Chef.mostrar(r.msg, true); return; }
      Chef.mostrar(r.msg, false);
      if (imprimirla) imprimirHoja(r.ticket);
      cargar();
    }, function () { Chef.mostrar('Para despachar se necesita conexión.', true); });
  }

  function entregado(d) {
    Chef.api('POST', '/api/domicilios/' + d.id_pedido + '/entregado').then(function (r) {
      Chef.mostrar(r.msg, !r.ok);
      cargar();
    }, function () { Chef.mostrar('Sin conexión. Intenta de nuevo.', true); });
  }

  /* --- hoja del domiciliario ------------------------------------------- */

  function hojaDe(d) {
    return {
      numero: d.numero, cliente: d.cliente, telefono: d.telefono, direccion: d.direccion, domiciliario: d.domiciliario,
      items: null, total: d.total, pagado: d.pagado, metodo_pago: d.metodo_pago, cobrar: d.cobrar,
      efectivo: d.efectivo, transferencia: d.transferencia, paga_con: d.paga_con, vuelto: d.vuelto
    };
  }

  function imprimirHoja(h) {
    var zona = $('impresion');
    zona.textContent = '';
    var t = el('section', 'ticket');
    t.appendChild(el('h2', '', 'DOMICILIO #' + h.numero));
    t.appendChild(el('p', '', 'Cliente: ' + (h.cliente || '-') + (h.telefono ? ' · Tel. ' + h.telefono : '')));
    t.appendChild(el('p', 'ticket__total', h.direccion));
    (h.items || []).forEach(function (i) {
      t.appendChild(el('p', 'ticket__linea', i.cantidad + ' × ' + i.nombre + '  ' + Chef.pesos(i.subtotal)));
    });
    t.appendChild(el('p', '', 'Total del pedido ' + Chef.pesos(h.total)));
    if (h.pagado) {
      t.appendChild(el('p', 'ticket__total', 'YA PAGADO: NO COBRAR'));
    } else if (h.metodo_pago === 'Efectivo') {
      t.appendChild(el('p', 'ticket__total', 'COBRAR ' + Chef.pesos(h.cobrar) + ' EN EFECTIVO'));
    } else if (h.metodo_pago === 'Mixto') {
      t.appendChild(el('p', 'ticket__total', 'COBRAR ' + Chef.pesos(h.efectivo) + ' EN EFECTIVO'));
      t.appendChild(el('p', '', 'y ' + Chef.pesos(h.transferencia) + ' por transferencia (no en billetes)'));
    } else {
      t.appendChild(el('p', 'ticket__total', 'PAGA ' + Chef.pesos(h.cobrar) + ' ' + (h.metodo_pago === 'Tarjeta' ? 'CON DATÁFONO' : 'POR NEQUI')));
      t.appendChild(el('p', '', 'No recibir efectivo.'));
    }
    if (h.paga_con) {
      t.appendChild(el('p', '', 'Paga con ' + Chef.pesos(h.paga_con)));
      t.appendChild(el('p', 'ticket__total', 'LLEVAR VUELTO ' + Chef.pesos(h.vuelto)));
    }
    t.appendChild(el('p', '', 'Lleva: ' + h.domiciliario + ' · ' +
      new Date().toLocaleTimeString('es-CO', { hour: '2-digit', minute: '2-digit' })));
    zona.appendChild(t);
    window.print();
  }

  /* --- domiciliarios --------------------------------------------------- */

  function editarDomiciliario(m) {
    var nombre = window.prompt('Nombre del domiciliario', m.nombre);
    if (nombre === null) return;
    var telefono = window.prompt('Celular (opcional)', m.telefono || '');
    if (telefono === null) return;
    Chef.api('PUT', '/api/domiciliarios/' + m.id_domiciliario, { nombre: nombre, telefono: telefono }).then(function (r) {
      Chef.mostrar(r.msg, !r.ok);
      cargar();
    }, function () { Chef.mostrar('Sin conexión. Intenta de nuevo.', true); });
  }

  function quitarDomiciliario(m) {
    if (!window.confirm('¿Quitar a ' + m.nombre + ' de los domiciliarios?')) return;
    Chef.api('DELETE', '/api/domiciliarios/' + m.id_domiciliario).then(function (r) {
      Chef.mostrar(r.msg, !r.ok);
      cargar();
    }, function () { Chef.mostrar('Sin conexión. Intenta de nuevo.', true); });
  }

  var form = $('form-domiciliario');
  if (form) {
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var cuerpo = { nombre: form.nombre.value.trim(), telefono: form.telefono.value.trim() };
      Chef.api('POST', '/api/domiciliarios', cuerpo).then(function (r) {
        Chef.mostrar(r.msg, !r.ok);
        if (r.ok) form.reset();
        cargar();
      }, function () { Chef.mostrar('Sin conexión. Intenta de nuevo.', true); });
    });
  }

  $('btn-nuevo-domicilio').addEventListener('click', function () {
    window.location.href = '/domicilio/' + Chef.uuid();
  });
  document.querySelectorAll('#filtros [data-filtro]').forEach(function (b) {
    b.addEventListener('click', function () {
      filtro = b.dataset.filtro;
      Chef.guardar(CLAVE_FILTRO, filtro);
      pintar();
    });
  });
  ['desp-metodo', 'desp-mixto', 'desp-paga-con'].forEach(function (id) {
    $(id).addEventListener('input', function () { if (id !== 'desp-paga-con') pintarBilletes(); recalcular(); });
  });
  $('dlg-despachar').addEventListener('close', function () {
    if ($('dlg-despachar').returnValue === 'ok') despachar();
  });
  document.addEventListener('visibilitychange', function () { if (!document.hidden) cargar(); });
  cargar();
  window.setInterval(function () { if (!document.hidden && !$('dlg-despachar').open) cargar(); }, 10000);
})();
