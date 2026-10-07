/* Caja > Domiciliarios: una tarjeta por domiciliario con los domicilios que
 * tiene en la calle y el efectivo exacto que debe entregar al regresar.
 * Rojo si pasa el tope. "Recibí" liquida los domicilios marcados: el
 * servidor revisa que la cifra siga siendo la misma (si cambio, avisa y no
 * registra nada). Se refresca cada 15 s sin perder lo marcado. */
(function () {
  'use strict';

  var raiz = document.getElementById('recaudo');
  if (!raiz) return;
  var esAdmin = raiz.dataset.admin === '1';
  var cajaAbierta = raiz.dataset.caja === '1';
  var METODOS = [['efectivo', 'Efectivo'], ['nequi', 'Nequi'], ['tarjeta', 'Tarjeta'], ['mixto', 'Mixto']];
  var CLAVES = { 'Efectivo': 'efectivo', 'Nequi/Daviplata': 'nequi', 'Tarjeta': 'tarjeta', 'Mixto': 'mixto' };
  var desmarcados = {};  // id_pedido -> true: lo que el cajero quito de este recaudo
  var ocupado = false;

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

  function pintar(datos) {
    var cont = $('recaudo-tarjetas');
    cont.textContent = '';
    $('recaudo-vacio').hidden = datos.domiciliarios.length > 0;
    $('recaudo-resumen').textContent = datos.domicilios_en_calle
      ? datos.domicilios_en_calle + ' domicilio(s) sin liquidar · ' + Chef.pesos(datos.efectivo_en_calle) +
        ' en efectivo en la calle. Esa plata todavía no está en el cajón ni en "Debe haber en efectivo".'
      : 'Nadie tiene plata pendiente por entregar.';
    if (esAdmin && document.activeElement !== $('recaudo-tope')) $('recaudo-tope').value = datos.tope;
    datos.domiciliarios.forEach(function (t) { cont.appendChild(tarjeta(t, datos.tope)); });
  }

  function tarjeta(t, tope) {
    var marcados = t.domicilios.filter(function (d) { return !desmarcados[d.id_pedido]; });
    var efectivo = marcados.reduce(function (s, d) { return s + d.efectivo; }, 0);
    var otros = marcados.reduce(function (s, d) { return s + d.transferencia; }, 0);
    var card = el('article', 'recaudo__tarjeta' + (t.alerta ? ' recaudo__tarjeta--alerta' : '') +
      (t.domicilios.length ? '' : ' recaudo__tarjeta--libre'));
    var cab = el('div', 'mesa__cab');
    cab.appendChild(el('strong', 'mesa__nombre', t.nombre));
    var estado = [];
    if (t.en_camino) estado.push(t.en_camino + ' en camino');
    if (t.entregados) estado.push(t.entregados + (t.entregados === 1 ? ' entregado' : ' entregados'));
    cab.appendChild(el('span', 'chip' + (t.domicilios.length ? '' : ' chip--ok'), estado.join(' · ') || 'Sin domicilios'));
    card.appendChild(cab);

    var cifra = el('div', 'recaudo__cifra');
    cifra.appendChild(el('small', '', 'Debe entregar en efectivo'));
    cifra.appendChild(el('strong', '', Chef.pesos(t.efectivo)));
    card.appendChild(cifra);
    if (t.alerta) {
      card.appendChild(el('p', 'recaudo__alerta', 'Lleva más de ' + Chef.pesos(tope) + ' en efectivo. Que entregue antes de volver a salir.'));
    }
    if (t.otros_medios) card.appendChild(el('p', 'muted', '+ ' + Chef.pesos(t.otros_medios) + ' por Nequi o tarjeta: confírmalo antes de recibir.'));

    if (t.domicilios.length) {
      var ul = el('ul', 'lineas recaudo__lista');
      t.domicilios.forEach(function (d) { ul.appendChild(fila(d)); });
      card.appendChild(ul);
      var recibir = boton(marcados.length ? 'Recibí ' + Chef.pesos(efectivo) + (marcados.length < t.domicilios.length ? ' (' + marcados.length + ' de ' + t.domicilios.length + ')' : '') : 'Marca un domicilio',
        'btn btn--primario btn--grande', function () { liquidar(t, marcados, efectivo, otros, recibir); });
      recibir.disabled = !marcados.length || ocupado;
      card.appendChild(recibir);
    }
    return card;
  }

  function fila(d) {
    var li = el('li', 'linea recaudo__domicilio');
    var marca = el('input');
    marca.type = 'checkbox';
    marca.checked = !desmarcados[d.id_pedido];
    marca.setAttribute('aria-label', 'Incluir domicilio #' + d.numero);
    marca.addEventListener('change', function () {
      if (marca.checked) delete desmarcados[d.id_pedido]; else desmarcados[d.id_pedido] = true;
      cargar();
    });
    li.appendChild(marca);
    var info = el('div', 'linea__info');
    info.appendChild(el('span', 'linea__nombre', '#' + d.numero + (d.cliente ? ' · ' + d.cliente : '')));
    info.appendChild(el('small', 'muted', (d.estado === 'entregado' ? 'Entregado' : 'En camino') + ' · ' + d.direccion));
    info.appendChild(boton('No lo entregó', 'btn-link btn-link--peligro recaudo__regresar', function () { regresar(d); }));
    li.appendChild(info);
    if (d.pagado) {
      li.appendChild(el('span', 'chip chip--ok', 'Pagado'));
    } else {
      var sel = el('select', 'recaudo__metodo');
      sel.setAttribute('aria-label', 'Cómo pagó el domicilio #' + d.numero);
      METODOS.forEach(function (m) {
        var o = el('option', '', m[1]);
        o.value = m[0];
        o.selected = CLAVES[d.metodo_pago] === m[0];
        sel.appendChild(o);
      });
      sel.addEventListener('change', function () { cambiarMetodo(d, sel); });
      li.appendChild(sel);
    }
    var valor = el('span', 'linea__valor', Chef.pesos(d.pagado ? 0 : d.total));
    if (d.metodo_pago === 'Mixto' && !d.pagado) valor.title = Chef.pesos(d.efectivo) + ' en efectivo';
    li.appendChild(valor);
    return li;
  }

  function cambiarMetodo(d, sel) {
    var cuerpo = { metodo: sel.value };
    if (sel.value === 'mixto') {
      var parte = window.prompt('¿Cuánto pagó en efectivo? (el resto por transferencia)', '');
      if (parte === null) { cargar(); return; }
      cuerpo.monto_efectivo = numero(parte);
    }
    Chef.api('POST', '/api/domicilios/' + d.id_pedido + '/metodo', cuerpo).then(function (r) {
      Chef.mostrar(r.msg, !r.ok);
      cargar();
    }, function () { Chef.mostrar('Sin conexión. Intenta de nuevo.', true); cargar(); });
  }

  function regresar(d) {
    var motivo = window.prompt('El domicilio #' + d.numero + ' vuelve a quedar por despachar. ¿Qué pasó?', 'El cliente no contestó');
    if (!motivo) return;
    Chef.api('POST', '/api/domicilios/' + d.id_pedido + '/regresar', { motivo: motivo }).then(function (r) {
      Chef.mostrar(r.msg, !r.ok);
      cargar();
    }, function () { Chef.mostrar('Sin conexión. Intenta de nuevo.', true); });
  }

  function liquidar(t, marcados, efectivo, otros, boton) {
    var porCobrar = marcados.some(function (d) { return !d.pagado; });
    if (porCobrar && !cajaAbierta) { Chef.mostrar('Abre la caja antes de recibir la plata.', true); return; }
    var texto = '¿' + t.nombre + ' te entregó ' + Chef.pesos(efectivo) + ' en efectivo?' +
      (otros ? '\nRevisa también que llegaron ' + Chef.pesos(otros) + ' por Nequi o tarjeta.' : '');
    if (!window.confirm(texto)) return;
    ocupado = true;
    boton.disabled = true;  // un doble toque no manda dos recaudos
    Chef.api('POST', '/api/domicilios/recaudo', {
      id_domiciliario: t.id_domiciliario,
      pedidos: marcados.map(function (d) { return d.id_pedido; }),
      efectivo: efectivo
    }).then(function (r) {
      ocupado = false;
      Chef.mostrar(r.msg, !r.ok);
      if (r.ok) { window.setTimeout(function () { window.location.reload(); }, 900); return; }
      cargar();
    }, function () { ocupado = false; Chef.mostrar('Sin conexión. No se registró nada; intenta de nuevo.', true); });
  }

  function cargar() {
    return Chef.api('GET', '/api/domicilios/recaudo').then(function (r) {
      if (!r.ok) { $('recaudo-resumen').textContent = r.msg || 'No se pudo cargar.'; return; }
      pintar(r);
    }, function () { $('recaudo-resumen').textContent = 'Sin conexión.'; });
  }

  var formTope = $('form-tope');
  if (formTope) {
    formTope.addEventListener('submit', function (ev) {
      ev.preventDefault();
      Chef.api('PUT', '/api/domicilios/tope', { tope: numero($('recaudo-tope').value) }).then(function (r) {
        Chef.mostrar(r.msg, !r.ok);
        cargar();
      }, function () { Chef.mostrar('Sin conexión. Intenta de nuevo.', true); });
    });
  }

  document.addEventListener('visibilitychange', function () { if (!document.hidden) cargar(); });
  cargar();
  window.setInterval(function () {
    var foco = document.activeElement;
    if (!document.hidden && !ocupado && !(foco && raiz.contains(foco) && foco.tagName === 'SELECT')) cargar();
  }, 15000);
})();
