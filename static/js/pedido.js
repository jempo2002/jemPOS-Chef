/* Pedido de una mesa: carta, cuenta, enviar a cocina, precuenta y cobro.
 *
 * Lo que se toca en la carta queda primero en "Por agregar" (borrador, se
 * guarda en el dispositivo). "Enviar a cocina" lo guarda en la cuenta y lo
 * manda a cocina, que es cuando se descuenta el inventario. Sin conexion,
 * todo eso queda en la cola (salon-comun.js) y se manda al volver internet.
 */
(function () {
  'use strict';

  var raiz = document.getElementById('pedido');
  var idMesa = raiz.dataset.mesa;
  var puedeCobrar = raiz.dataset.cobrar === '1';
  var esAdmin = raiz.dataset.admin === '1';
  var CLAVE_CARTA = 'chef_carta_v1';
  var CLAVE_PEDIDO = 'chef_pedido_v1_' + idMesa;
  var CLAVE_BORRADOR = 'chef_borrador_v1_' + idMesa;
  var ESTADOS = { pendiente: 'Sin enviar', enviado: 'En cocina', listo: 'Listo', entregado: 'Entregado', anulado: 'Anulado' };

  var carta = Chef.leer(CLAVE_CARTA, []);
  var detalle = Chef.leer(CLAVE_PEDIDO, null);
  var borrador = Chef.leer(CLAVE_BORRADOR, []);
  var categoria = 'todas';
  var ultimasComandas = [];

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

  function numero(valor) { return Number(String(valor || '').replace(/\D/g, '')) || 0; }

  function guardarBorrador() { Chef.guardar(CLAVE_BORRADOR, borrador); }

  function nombreProducto(id) {
    var p = carta.find(function (x) { return String(x.id_producto) === String(id); });
    return p ? p.nombre : 'Producto';
  }

  function precioProducto(id) {
    var p = carta.find(function (x) { return String(x.id_producto) === String(id); });
    return p ? p.precio_venta : 0;
  }

  /* Lineas guardadas en la cola sin conexion para esta mesa. */
  function lineasEnCola() {
    var lineas = [];
    Chef.pendientesDeMesa(idMesa).forEach(function (op) {
      if (!/\/items$/.test(op.url) || !op.cuerpo) return;
      op.cuerpo.items.forEach(function (i) {
        lineas.push({ nombre: nombreProducto(i.id_producto), cantidad: i.cantidad, nota: i.nota,
          subtotal: i.cantidad * precioProducto(i.id_producto), estado: op.estado === 'fallo' ? 'fallo' : 'cola' });
      });
    });
    return lineas;
  }

  /* --- carta ----------------------------------------------------------- */

  function pintarCarta() {
    var filtro = $('buscar').value.trim().toLowerCase();
    var nav = $('categorias');
    nav.textContent = '';
    var cats = [];
    carta.forEach(function (p) { if (p.categoria && cats.indexOf(p.categoria) === -1) cats.push(p.categoria); });
    if (cats.length) {
      ['todas'].concat(cats).forEach(function (c) {
        nav.appendChild(boton(c === 'todas' ? 'Todo' : c, 'pestana' + (c === categoria ? ' pestana--activa' : ''), function () {
          categoria = c; pintarCarta();
        }));
      });
    }
    var cont = $('productos');
    cont.textContent = '';
    $('carta-vacia').hidden = carta.length > 0;
    carta.forEach(function (p) {
      if (categoria !== 'todas' && p.categoria !== categoria) return;
      if (filtro && p.nombre.toLowerCase().indexOf(filtro) === -1) return;
      var b = boton('', 'producto', function () { agregar(p); });
      b.appendChild(el('span', 'producto__nombre', p.nombre));
      b.appendChild(el('span', 'producto__precio', Chef.pesos(p.precio_venta)));
      // Stock propio, o cuantos platos alcanzan con los ingredientes (receta).
      var quedan = p.stock_actual !== null && p.stock_actual !== undefined ? p.stock_actual : p.disponibles;
      if (quedan !== null && quedan !== undefined) {
        b.appendChild(el('small', 'producto__stock' + (quedan <= 0 ? ' producto__stock--agotado' : ''),
          quedan <= 0 ? 'Agotado' : 'Quedan ' + quedan));
      }
      cont.appendChild(b);
    });
  }

  function agregar(p) {
    var linea = borrador.find(function (l) { return l.id_producto === p.id_producto && !l.nota; });
    if (linea) linea.cantidad += 1;
    else borrador.push({ uuid: Chef.uuid(), id_producto: p.id_producto, nombre: p.nombre, precio: p.precio_venta, cantidad: 1, nota: '' });
    guardarBorrador();
    pintarCuenta();
  }

  /* --- cuenta ---------------------------------------------------------- */

  function pintarCuenta() {
    var mesa = detalle && detalle.mesa;
    var pedido = detalle && detalle.pedido;
    $('titulo-mesa').textContent = 'Mesa ' + (mesa ? mesa.nombre : '');
    $('info-pedido').textContent = pedido
      ? (pedido.estado === 'por_cobrar' ? 'Pidió la cuenta' : 'Abierta') + ' desde las ' + pedido.abierto_en +
        ' · ' + (pedido.mesero || '') + (pedido.comensales ? ' · ' + pedido.comensales + ' personas' : '')
      : 'Mesa libre. Toca un plato para empezar.';
    if (!navigator.onLine) $('info-pedido').textContent += ' · sin conexión';

    var ul = $('lineas');
    ul.textContent = '';
    var total = detalle ? detalle.total : 0;
    (detalle ? detalle.items : []).forEach(function (i) {
      var li = el('li', 'linea linea--' + i.estado);
      var info = el('div', 'linea__info');
      info.appendChild(el('span', 'linea__nombre', i.cantidad + ' × ' + i.nombre));
      if (i.nota) info.appendChild(el('small', 'linea__nota', i.nota));
      var estado = ESTADOS[i.estado] + (i.numero_comanda ? ' · #' + i.numero_comanda : '');
      info.appendChild(el('small', 'chip chip--' + i.estado, estado));
      li.appendChild(info);
      li.appendChild(el('span', 'linea__valor', Chef.pesos(i.subtotal)));
      if (i.estado !== 'anulado' && (i.estado === 'pendiente' || esAdmin)) {
        li.appendChild(boton('Quitar', 'btn-link btn-link--peligro', function () { abrirAnular(i); }));
      }
      ul.appendChild(li);
    });
    lineasEnCola().forEach(function (i) {
      total += i.subtotal;
      var li = el('li', 'linea linea--cola');
      var info = el('div', 'linea__info');
      info.appendChild(el('span', 'linea__nombre', i.cantidad + ' × ' + i.nombre));
      info.appendChild(el('small', 'chip ' + (i.estado === 'fallo' ? 'chip--error' : 'chip--offline'),
        i.estado === 'fallo' ? 'No se pudo guardar' : 'Guardado sin conexión'));
      li.appendChild(info);
      li.appendChild(el('span', 'linea__valor', Chef.pesos(i.subtotal)));
      ul.appendChild(li);
    });

    var nuevos = $('nuevos');
    nuevos.textContent = '';
    $('bloque-nuevos').hidden = borrador.length === 0;
    borrador.forEach(function (l, idx) {
      total += l.precio * l.cantidad;
      var li = el('li', 'linea linea--nueva');
      var info = el('div', 'linea__info');
      info.appendChild(el('span', 'linea__nombre', l.nombre));
      var nota = el('input', 'linea__nota-input');
      nota.placeholder = 'Nota para cocina';
      nota.maxLength = 150;
      nota.value = l.nota;
      nota.addEventListener('change', function () { l.nota = nota.value.trim(); guardarBorrador(); });
      info.appendChild(nota);
      li.appendChild(info);
      var cant = el('div', 'cantidad');
      cant.appendChild(boton('−', 'btn btn--mini', function () {
        l.cantidad -= 1;
        if (l.cantidad <= 0) borrador.splice(idx, 1);
        guardarBorrador(); pintarCuenta();
      }));
      cant.appendChild(el('span', '', String(l.cantidad)));
      cant.appendChild(boton('+', 'btn btn--mini', function () { l.cantidad += 1; guardarBorrador(); pintarCuenta(); }));
      li.appendChild(cant);
      li.appendChild(el('span', 'linea__valor', Chef.pesos(l.precio * l.cantidad)));
      nuevos.appendChild(li);
    });

    $('total').textContent = Chef.pesos(total);
    var hayCuenta = !!pedido;
    $('btn-mover').hidden = !hayCuenta;
    $('btn-anular-cuenta').hidden = !hayCuenta;
    $('btn-cobrar').hidden = !(puedeCobrar && hayCuenta);
    $('btn-precuenta').hidden = !hayCuenta;
    $('btn-guardar').hidden = borrador.length === 0;
    var porEnviar = borrador.length + (detalle ? detalle.sin_enviar : 0);
    $('btn-enviar').disabled = porEnviar === 0;
    $('btn-enviar').textContent = porEnviar ? 'Enviar a cocina' : 'Nada por enviar';
  }

  function cargar() {
    return Chef.api('GET', '/api/mesas/' + idMesa + '/pedido').then(function (datos) {
      if (!datos.ok) { Chef.mostrar(datos.msg || 'No se pudo cargar la mesa.', true); return; }
      detalle = datos;
      Chef.guardar(CLAVE_PEDIDO, detalle);
      pintarCuenta();
    }, function () { pintarCuenta(); });
  }

  function cargarCarta() {
    return Chef.api('GET', '/api/carta').then(function (datos) {
      if (datos.ok) { carta = datos.productos; Chef.guardar(CLAVE_CARTA, carta); }
      pintarCarta();
    }, pintarCarta);
  }

  /* --- acciones ------------------------------------------------------- */

  function etiqueta(texto) { return 'Mesa ' + (detalle && detalle.mesa ? detalle.mesa.nombre : idMesa) + ': ' + texto; }

  function guardarItems() {
    if (!borrador.length) return Promise.resolve(true);
    var cuerpo = { items: borrador.map(function (l) {
      return { id_producto: l.id_producto, cantidad: l.cantidad, nota: l.nota || null, uuid: l.uuid };
    }) };
    var n = borrador.reduce(function (s, l) { return s + l.cantidad; }, 0);
    return Chef.enviar('POST', '/api/mesas/' + idMesa + '/items', cuerpo, { etiqueta: etiqueta(n + ' producto(s)'), mesa: idMesa })
      .then(function (datos) {
        if (datos.offline) {
          borrador = []; guardarBorrador(); pintarCuenta();
          Chef.mostrar('Sin conexión: el pedido quedó guardado en este dispositivo y se envía al volver internet.', false);
          return true;
        }
        if (!datos.ok) { Chef.mostrar(datos.msg || 'No se pudo guardar.', true); return false; }
        detalle = datos;
        Chef.guardar(CLAVE_PEDIDO, detalle);
        borrador = []; guardarBorrador(); pintarCuenta();
        if (datos.avisos && datos.avisos.length) Chef.mostrar('Ojo con el inventario: ' + datos.avisos.join(' '), true);
        return true;
      });
  }

  function enviarCocina() {
    var btn = $('btn-enviar');
    btn.disabled = true;
    guardarItems().then(function (ok) {
      if (!ok) return null;
      return Chef.enviar('POST', '/api/mesas/' + idMesa + '/comanda', null, { etiqueta: etiqueta('enviar a cocina'), mesa: idMesa })
        .then(function (datos) {
          if (datos.offline) return;
          if (!datos.ok) { Chef.mostrar(datos.msg, true); return; }
          ultimasComandas = datos.comandas || [];
          Chef.mostrar(datos.msg + (ultimasComandas.length ? ' Comanda #' + ultimasComandas.map(function (c) { return c.numero; }).join(', #') : ''), false);
          $('btn-imprimir-comanda').hidden = ultimasComandas.length === 0;
          return cargar();
        });
    }).finally(function () { pintarCuenta(); });
  }

  function imprimir(construir) {
    var zona = $('impresion');
    zona.textContent = '';
    construir(zona);
    window.print();
  }

  function imprimirComandas() {
    imprimir(function (zona) {
      ultimasComandas.forEach(function (c) {
        var bloque = el('section', 'ticket');
        bloque.appendChild(el('h2', '', (c.estacion === 'bar' ? 'BAR' : 'COCINA') + ' #' + c.numero));
        bloque.appendChild(el('p', '', 'Mesa ' + c.mesa + ' · ' + new Date().toLocaleTimeString('es-CO', { hour: '2-digit', minute: '2-digit' })));
        c.items.forEach(function (i) {
          bloque.appendChild(el('p', 'ticket__linea', i.cantidad + ' × ' + i.nombre));
          if (i.nota) bloque.appendChild(el('p', 'ticket__nota', '  → ' + i.nota));
        });
        zona.appendChild(bloque);
      });
    });
  }

  function imprimirPrecuenta(d) {
    imprimir(function (zona) {
      var t = el('section', 'ticket');
      t.appendChild(el('h2', '', 'Precuenta · Mesa ' + d.mesa.nombre));
      t.appendChild(el('p', '', 'Documento sin valor fiscal'));
      d.items.forEach(function (i) {
        if (i.estado === 'anulado') return;
        t.appendChild(el('p', 'ticket__linea', i.cantidad + ' × ' + i.nombre + '  ' + Chef.pesos(i.subtotal)));
      });
      t.appendChild(el('p', 'ticket__total', 'Total ' + Chef.pesos(d.total)));
      t.appendChild(el('p', '', 'Propina sugerida (10 %, voluntaria) ' + Chef.pesos(d.propina_sugerida)));
      t.appendChild(el('p', 'ticket__total', 'Total con propina ' + Chef.pesos(d.total + d.propina_sugerida)));
      zona.appendChild(t);
    });
  }

  function precuenta() {
    if (borrador.length) { Chef.mostrar('Primero envía o borra lo que está por agregar.', true); return; }
    Chef.api('POST', '/api/mesas/' + idMesa + '/precuenta').then(function (datos) {
      if (!datos.ok) { Chef.mostrar(datos.msg, true); return; }
      detalle = datos; Chef.guardar(CLAVE_PEDIDO, detalle); pintarCuenta();
      imprimirPrecuenta(datos);
    }, function () {
      if (detalle && detalle.pedido) imprimirPrecuenta(detalle);
    });
  }

  /* --- cobro ----------------------------------------------------------- */

  function recalcularCobro() {
    var total = detalle.total;
    var propina = numero($('cobro-propina').value);
    var aPagar = total + propina;
    var metodo = $('cobro-metodo').value;
    $('cobro-apagar').textContent = Chef.pesos(aPagar);
    $('cobro-mixto').hidden = metodo !== 'mixto';
    $('bloque-recibido').hidden = metodo !== 'efectivo';
    if (metodo === 'mixto') $('cobro-transferencia').value = Math.max(0, aPagar - numero($('cobro-efectivo').value));
    var recibido = numero($('cobro-recibido').value);
    $('cobro-cambio').textContent = metodo === 'efectivo' && recibido >= aPagar && recibido ? 'Cambio ' + Chef.pesos(recibido - aPagar) : '';
  }

  function abrirCobro() {
    if (borrador.length || (detalle && detalle.sin_enviar)) {
      Chef.mostrar('Hay productos sin enviar a cocina. Envíalos o quítalos antes de cobrar.', true);
      return;
    }
    if (!detalle || !detalle.pedido) return;
    $('cobro-mesa').textContent = 'mesa ' + detalle.mesa.nombre;
    $('cobro-total').textContent = Chef.pesos(detalle.total);
    $('cobro-propina').value = detalle.propina_sugerida;
    $('cobro-recibido').value = '';
    $('cobro-efectivo').value = '';
    recalcularCobro();
    $('dlg-cobro').showModal();
  }

  function cobrar() {
    var metodo = $('cobro-metodo').value;
    var cuerpo = {
      metodo: metodo,
      propina: numero($('cobro-propina').value),
      uuid: Chef.uuid(),
      id_pedido: detalle.pedido.id_pedido
    };
    if (metodo === 'mixto') {
      cuerpo.monto_efectivo = numero($('cobro-efectivo').value);
      cuerpo.monto_transferencia = numero($('cobro-transferencia').value);
    }
    Chef.enviar('POST', '/api/mesas/' + idMesa + '/cobrar', cuerpo, { etiqueta: etiqueta('cobro'), mesa: idMesa })
      .then(function (datos) {
        if (datos.offline) {
          Chef.mostrar('Sin conexión: el cobro quedó guardado y se registra al volver internet.', false);
          return;
        }
        if (!datos.ok) { Chef.mostrar(datos.msg, true); return; }
        Chef.mostrar(datos.msg, false);
        window.localStorage.removeItem(CLAVE_PEDIDO);
        window.setTimeout(function () { window.location.href = '/mesas'; }, 900);
      });
  }

  /* --- mover y anular -------------------------------------------------- */

  function abrirMover() {
    var sel = $('mover-destino');
    sel.textContent = '';
    Chef.api('GET', '/api/mesas/plano').then(function (datos) {
      datos.mesas.forEach(function (m) {
        if (String(m.id_mesa) === String(idMesa)) return;
        var o = el('option', '', m.nombre + (m.estado === 'libre' ? ' (libre)' : ' (ocupada: se unen)'));
        o.value = m.id_mesa;
        sel.appendChild(o);
      });
      $('dlg-mover').showModal();
    }, function () { Chef.mostrar('Para mover mesas se necesita conexión.', true); });
  }

  function mover() {
    var destino = $('mover-destino').value;
    Chef.api('POST', '/api/mesas/' + idMesa + '/mover', { id_mesa_destino: destino }).then(function (datos) {
      if (!datos.ok) { Chef.mostrar(datos.msg, true); return; }
      window.location.href = '/mesas/' + destino;
    }, function () { Chef.mostrar('Para mover mesas se necesita conexión.', true); });
  }

  var anulando = null;

  function abrirAnular(item) {
    anulando = item;
    var enviado = item && item.estado !== 'pendiente';
    $('anular-titulo').textContent = item ? 'Quitar ' + item.nombre : 'Anular la cuenta';
    $('anular-ayuda').textContent = !item
      ? 'La mesa queda libre sin cobrar. Solo se puede si nada fue a cocina.'
      : (enviado ? 'Ya fue a cocina. Si se cocinó, queda como pérdida (merma).' : 'Aún no fue a cocina: se quita sin costo.');
    $('bloque-devolver').hidden = !enviado;
    $('anular-devolver').checked = false;
    $('anular-motivo').value = '';
    $('anular-motivo').required = !item || enviado;
    $('dlg-anular').showModal();
  }

  function anular() {
    var cuerpo = { motivo: $('anular-motivo').value.trim(), devolver: $('anular-devolver').checked };
    var url = anulando ? '/api/pedido-items/' + anulando.id_item + '/anular' : '/api/mesas/' + idMesa + '/anular';
    Chef.api('POST', url, cuerpo).then(function (datos) {
      if (!datos.ok) { Chef.mostrar(datos.msg, true); return; }
      Chef.mostrar(datos.msg, false);
      if (!anulando) { window.location.href = '/mesas'; return; }
      cargar();
    }, function () { Chef.mostrar('Para anular se necesita conexión.', true); });
  }

  function alCerrar(dlg, accion) {
    $(dlg).addEventListener('close', function () { if ($(dlg).returnValue === 'ok') accion(); });
  }

  $('buscar').addEventListener('input', pintarCarta);
  $('btn-enviar').addEventListener('click', enviarCocina);
  $('btn-guardar').addEventListener('click', function () { guardarItems(); });
  $('btn-precuenta').addEventListener('click', precuenta);
  $('btn-imprimir-comanda').addEventListener('click', imprimirComandas);
  $('btn-cobrar').addEventListener('click', abrirCobro);
  $('btn-mover').addEventListener('click', abrirMover);
  $('btn-anular-cuenta').addEventListener('click', function () { abrirAnular(null); });
  ['cobro-propina', 'cobro-metodo', 'cobro-efectivo', 'cobro-recibido'].forEach(function (id) {
    $(id).addEventListener('input', recalcularCobro);
  });
  $('propina-sugerida').addEventListener('click', function () { $('cobro-propina').value = detalle.propina_sugerida; recalcularCobro(); });
  $('propina-cero').addEventListener('click', function () { $('cobro-propina').value = 0; recalcularCobro(); });
  alCerrar('dlg-cobro', cobrar);
  alCerrar('dlg-mover', mover);
  alCerrar('dlg-anular', anular);
  document.addEventListener('chef:sincronizado', cargar);

  pintarCarta();
  pintarCuenta();
  cargarCarta();
  cargar();
})();
