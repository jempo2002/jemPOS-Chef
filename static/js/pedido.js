/* Pedido de una mesa, para llevar o domicilio: carta, cuenta, enviar a
 * cocina, precuenta, cobro y cuenta dividida.
 *
 * Un domicilio es un para llevar con direccion (/domicilio/<uuid>). Se
 * despacha desde Domicilios; mientras va en camino esta pantalla solo lo
 * muestra: el pago se recibe en Caja al regresar el domiciliario.
 *
 * La misma pantalla sirve para los dos: una mesa trabaja en /api/mesas/<id> y
 * un para llevar en /api/llevar/<uuid> (el uuid lo pone el dispositivo, asi
 * que se puede abrir sin conexion).
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
  var llevar = raiz.dataset.llevar;
  var puedeCobrar = raiz.dataset.cobrar === '1';
  var esAdmin = raiz.dataset.admin === '1';
  var puedeDividir = raiz.dataset.dividir === '1';
  var API = llevar ? '/api/llevar/' + llevar : '/api/mesas/' + idMesa;
  var COLA = llevar ? 'llevar:' + llevar : idMesa;  // agrupa lo guardado sin conexion
  var CLAVE_CARTA = 'chef_carta_v1';
  var CLAVE_PEDIDO = 'chef_pedido_v1_' + (llevar || idMesa);
  var CLAVE_BORRADOR = 'chef_borrador_v1_' + (llevar || idMesa);
  var CLAVE_CLIENTE = 'chef_cliente_v1_' + llevar;
  var abiertoComoDomicilio = raiz.dataset.domicilio === '1';
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

  /* Lineas guardadas en la cola sin conexion para este pedido. */
  function lineasEnCola() {
    var lineas = [];
    Chef.pendientesDeMesa(COLA).forEach(function (op) {
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
    if (detalle && detalle.pedido && (detalle.pedido.estado === 'cerrado' || detalle.pedido.estado === 'anulado')) {
      Chef.mostrar('Este pedido ya está cerrado.', true);
      return;
    }
    if (enCaminoDomicilio()) {
      Chef.mostrar('Este domicilio ya salió. Para agregar algo, abre otro pedido.', true);
      return;
    }
    var linea = borrador.find(function (l) { return l.id_producto === p.id_producto && !l.nota; });
    if (linea) linea.cantidad += 1;
    else borrador.push({ uuid: Chef.uuid(), id_producto: p.id_producto, nombre: p.nombre, precio: p.precio_venta, cantidad: 1, nota: '' });
    guardarBorrador();
    pintarCuenta();
  }

  /* --- cuenta ---------------------------------------------------------- */

  function pintarCuenta() {
    var pedido = detalle && detalle.pedido;
    var cerrado = !!pedido && (pedido.estado === 'cerrado' || pedido.estado === 'anulado');
    $('titulo-mesa').textContent = titulo();
    var ESTADO_PEDIDO = { abierto: 'Abierta', por_cobrar: 'Pidió la cuenta', cerrado: 'Pagado', anulado: 'Anulado' };
    $('info-pedido').textContent = pedido
      ? ESTADO_PEDIDO[pedido.estado] + (pedido.entregado ? ' y entregado' : '') + ' · desde las ' + pedido.abierto_en +
        ' · ' + (pedido.mesero || '') + (pedido.comensales ? ' · ' + pedido.comensales + ' personas' : '')
      : (llevar ? (esDomicilio() ? 'Domicilio nuevo. Escribe la dirección y toca un plato para empezar.' : 'Pedido nuevo. Toca un plato para empezar.')
        : 'Mesa libre. Toca un plato para empezar.');
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
      if (!cerrado && i.estado !== 'anulado' && (i.estado === 'pendiente' || esAdmin)) {
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
    var hayCuenta = !!pedido && !cerrado;
    $('btn-mover').hidden = !hayCuenta || !!llevar;
    // Un domicilio se despacha y se entrega desde Domicilios; en camino se cobra en Caja.
    var enCamino = enCaminoDomicilio();
    $('btn-entregar').hidden = !llevar || esDomicilio() || !pedido || pedido.entregado || pedido.estado === 'anulado';
    $('btn-anular-cuenta').hidden = !hayCuenta || enCamino;
    $('btn-cobrar').hidden = !(puedeCobrar && hayCuenta) || enCamino;
    $('btn-dividir').hidden = !(puedeCobrar && puedeDividir && hayCuenta) || enCamino;
    pintarAvisoDomicilio(enCamino);
    $('btn-precuenta').hidden = !hayCuenta;
    $('btn-enviar').hidden = cerrado;
    if (llevar) $('bloque-cliente').hidden = cerrado || enCamino;
    $('productos').classList.toggle('productos--bloqueados', cerrado || enCamino);
    if (enCamino) $('btn-enviar').hidden = true;
    $('btn-guardar').hidden = borrador.length === 0;
    var porEnviar = borrador.length + (detalle ? detalle.sin_enviar : 0);
    $('btn-enviar').disabled = porEnviar === 0;
    $('btn-enviar').textContent = porEnviar ? 'Enviar a cocina' : 'Nada por enviar';
  }

  function titulo() {
    if (detalle && detalle.lugar && (detalle.pedido || !llevar)) return detalle.lugar.titulo;
    return llevar ? (esDomicilio() ? 'Domicilio nuevo' : 'Para llevar') : 'Mesa';
  }

  /* --- domicilio ------------------------------------------------------- */

  function esDomicilio() {
    // Antes del primer plato el servidor todavia no sabe que es un domicilio.
    if (detalle && detalle.pedido && detalle.lugar) return detalle.lugar.tipo === 'domicilio';
    return abiertoComoDomicilio;
  }

  function datosDomicilio() { return (detalle && detalle.lugar && detalle.lugar.domicilio) || null; }

  function enCaminoDomicilio() {
    var d = datosDomicilio();
    return !!d && (d.estado === 'despachado' || d.estado === 'entregado');
  }

  function pintarAvisoDomicilio(enCamino) {
    var aviso = $('aviso-domicilio');
    if (!aviso) return;
    var d = datosDomicilio();
    if (!d || d.estado === 'por_despachar' || d.estado === 'liquidado') { aviso.hidden = true; return; }
    aviso.textContent = (d.estado === 'entregado' ? 'Entregado por ' : 'En camino con ') + d.domiciliario + ' a ' + d.direccion +
      '. ' + (enCamino ? 'El pago se recibe en Caja cuando regrese el domiciliario.' : '');
    aviso.hidden = false;
  }

  function pintarCliente() {
    if (!llevar) return;
    var lugar = detalle && detalle.lugar;
    var guardado = Chef.leer(CLAVE_CLIENTE, {});
    $('cliente-nombre').value = guardado.cliente || (lugar && lugar.cliente) || '';
    $('cliente-telefono').value = guardado.telefono || (lugar && lugar.telefono) || '';
    $('bloque-direccion').hidden = !esDomicilio();
    var dom = datosDomicilio();
    $('cliente-direccion').value = guardado.direccion || (dom && dom.direccion) || '';
    $('cliente-direccion').required = esDomicilio();
    if (esDomicilio()) $('volver').href = '/domicilios';
  }

  function cargar() {
    return Chef.api('GET', API + '/pedido').then(function (datos) {
      if (!datos.ok) { Chef.mostrar(datos.msg || 'No se pudo cargar la mesa.', true); return; }
      detalle = datos;
      Chef.guardar(CLAVE_PEDIDO, detalle);
      pintarCliente();
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

  function etiqueta(texto) { return titulo() + ': ' + texto; }

  function guardarItems() {
    if (!borrador.length) return Promise.resolve(true);
    var cuerpo = { items: borrador.map(function (l) {
      return { id_producto: l.id_producto, cantidad: l.cantidad, nota: l.nota || null, uuid: l.uuid };
    }) };
    if (llevar) {
      cuerpo.cliente = $('cliente-nombre').value.trim() || null;
      cuerpo.telefono = $('cliente-telefono').value.trim() || null;
    }
    if (llevar && esDomicilio()) {
      cuerpo.domicilio = true;
      cuerpo.direccion = $('cliente-direccion').value.trim() || null;
      if (!cuerpo.direccion && !(detalle && detalle.pedido)) {
        Chef.mostrar('Escribe la dirección del domicilio.', true);
        $('cliente-direccion').focus();
        return Promise.resolve(false);
      }
    }
    var n = borrador.reduce(function (s, l) { return s + l.cantidad; }, 0);
    return Chef.enviar('POST', API + '/items', cuerpo, { etiqueta: etiqueta(n + ' producto(s)'), mesa: COLA })
      .then(function (datos) {
        if (datos.offline) {
          borrador = []; guardarBorrador(); pintarCuenta();
          Chef.mostrar('Sin conexión: el pedido quedó guardado en este dispositivo y se envía al volver internet.', false);
          return true;
        }
        if (!datos.ok) { Chef.mostrar(datos.msg || 'No se pudo guardar.', true); return false; }
        detalle = datos;
        Chef.guardar(CLAVE_PEDIDO, detalle);
        if (llevar) window.localStorage.removeItem(CLAVE_CLIENTE);
        borrador = []; guardarBorrador(); pintarCliente(); pintarCuenta();
        if (datos.avisos && datos.avisos.length) Chef.mostrar('Ojo con el inventario: ' + datos.avisos.join(' '), true);
        return true;
      });
  }

  function enviarCocina() {
    var btn = $('btn-enviar');
    btn.disabled = true;
    guardarItems().then(function (ok) {
      if (!ok) return null;
      return Chef.enviar('POST', API + '/comanda', null, { etiqueta: etiqueta('enviar a cocina'), mesa: COLA })
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
        bloque.appendChild(el('p', '', c.lugar + ' · ' + new Date().toLocaleTimeString('es-CO', { hour: '2-digit', minute: '2-digit' })));
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
      t.appendChild(el('h2', '', 'Precuenta · ' + d.lugar.titulo));
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
    Chef.api('POST', API + '/precuenta').then(function (datos) {
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
    $('cobro-mesa').textContent = titulo();
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
    Chef.enviar('POST', API + '/cobrar', cuerpo, { etiqueta: etiqueta('cobro'), mesa: COLA }).then(alCobrar);
  }

  function alCobrar(datos) {
    if (datos.offline) {
      Chef.mostrar('Sin conexión: el cobro quedó guardado y se registra al volver internet.', false);
      return;
    }
    if (!datos.ok) { Chef.mostrar(datos.msg, true); return; }
    Chef.mostrar(datos.msg, false);
    window.localStorage.removeItem(CLAVE_PEDIDO);
    window.setTimeout(function () { window.location.href = esDomicilio() ? '/domicilios' : '/mesas'; }, 900);
  }

  /* --- cuenta dividida ------------------------------------------------- *
   * Por productos: cada cuenta toma unidades de las lineas (una linea de 2
   * se puede partir 1 y 1) y se cobra como una venta propia. En partes
   * iguales el total se parte en N y queda una sola venta. El servidor
   * repite las cuentas; aqui solo se muestran. */

  var division = null;

  function nuevaParte(n) { return { etiqueta: 'Cuenta ' + n, metodo: 'efectivo', propina: 0, items: {} }; }

  function lineasDivisibles() { return detalle.items.filter(function (i) { return i.estado !== 'anulado'; }); }

  function repartido(idItem) {
    return division.partes.reduce(function (s, p) { return s + (p.items[idItem] || 0); }, 0);
  }

  function redondear(x) { return Math.round(x * 1000) / 1000; }

  function montosPartes() {
    if (division.modo === 'iguales') {
      var n = division.partes.length;
      var base = Math.floor(detalle.total / n);
      var sobra = detalle.total - base * n;
      return division.partes.map(function (_, i) { return base + (i < sobra ? 1 : 0); });
    }
    return division.partes.map(function (p) {
      return lineasDivisibles().reduce(function (s, i) {
        return s + Math.round((p.items[i.id_item] || 0) * i.precio_unitario);
      }, 0);
    });
  }

  // La propina de cada parte arranca en 0: solo se cobra si el cliente la pide.
  function propinaDe(p) { return p.propina || 0; }

  function abrirDividir() {
    if (borrador.length || (detalle && detalle.sin_enviar)) {
      Chef.mostrar('Hay productos sin enviar a cocina. Envíalos o quítalos antes de cobrar.', true);
      return;
    }
    if (!detalle || !detalle.pedido) return;
    if (!division || division.id_pedido !== detalle.pedido.id_pedido) {
      var n = Math.max(2, Math.min(20, detalle.pedido.comensales || 2));
      division = { id_pedido: detalle.pedido.id_pedido, modo: 'items', activa: 0, partes: [] };
      for (var k = 1; k <= n; k++) division.partes.push(nuevaParte(k));
    }
    $('div-total').textContent = 'Total ' + Chef.pesos(detalle.total);
    pintarDivision();
    $('dlg-dividir').showModal();
  }

  function pintarDivision() {
    var porItems = division.modo === 'items';
    document.querySelectorAll('#form-dividir [data-modo]').forEach(function (b) {
      b.classList.toggle('pestana--activa', b.dataset.modo === division.modo);
    });
    $('div-items').hidden = !porItems;
    $('div-iguales').hidden = porItems;
    $('div-n').textContent = division.partes.length;
    if (division.activa >= division.partes.length) division.activa = 0;

    if (porItems) {
      var nav = $('div-cuentas');
      nav.textContent = '';
      division.partes.forEach(function (p, idx) {
        nav.appendChild(boton(p.etiqueta, 'pestana' + (idx === division.activa ? ' pestana--activa' : ''), function () {
          division.activa = idx; pintarDivision();
        }));
      });
      if (division.partes.length < 20) {
        nav.appendChild(boton('+ Persona', 'pestana', function () {
          division.partes.push(nuevaParte(division.partes.length + 1));
          division.activa = division.partes.length - 1;
          pintarDivision();
        }));
      }
      var activa = division.partes[division.activa];
      $('div-activa').textContent = 'Paga ' + activa.etiqueta;
      var sin = $('div-sin');
      var asignados = $('div-asignados');
      sin.textContent = '';
      asignados.textContent = '';
      lineasDivisibles().forEach(function (i) {
        var queda = redondear(i.cantidad - repartido(i.id_item));
        if (queda > 0) {
          var li = el('li', 'linea');
          li.appendChild(el('span', 'linea__info', queda + ' × ' + i.nombre));
          li.appendChild(el('span', 'linea__valor', Chef.pesos(queda * i.precio_unitario)));
          li.addEventListener('click', function () {
            activa.items[i.id_item] = redondear((activa.items[i.id_item] || 0) + Math.min(1, queda));
            pintarDivision();
          });
          sin.appendChild(li);
        }
        var mia = activa.items[i.id_item] || 0;
        if (mia > 0) {
          var li2 = el('li', 'linea');
          li2.appendChild(el('span', 'linea__info', mia + ' × ' + i.nombre));
          li2.appendChild(el('span', 'linea__valor', Chef.pesos(mia * i.precio_unitario)));
          li2.addEventListener('click', function () {
            var resto = redondear(mia - Math.min(1, mia));
            if (resto > 0) activa.items[i.id_item] = resto; else delete activa.items[i.id_item];
            pintarDivision();
          });
          asignados.appendChild(li2);
        }
      });
      if (!sin.children.length) sin.appendChild(el('li', 'muted', 'Todo repartido.'));
      if (!asignados.children.length) asignados.appendChild(el('li', 'muted', 'Toca productos de la izquierda.'));
    }

    var montos = montosPartes();
    var lista = $('div-partes');
    lista.textContent = '';
    var aPagar = 0;
    division.partes.forEach(function (p, idx) {
      var monto = montos[idx];
      var propina = propinaDe(p);
      aPagar += monto + propina;
      var li = el('li', 'linea parte');
      var nombre = el('input', 'parte__nombre');
      nombre.value = p.etiqueta;
      nombre.maxLength = 40;
      nombre.setAttribute('aria-label', 'Nombre de la cuenta');
      nombre.addEventListener('change', function () { p.etiqueta = nombre.value.trim() || 'Cuenta ' + (idx + 1); pintarDivision(); });
      li.appendChild(nombre);
      li.appendChild(el('span', 'linea__valor', Chef.pesos(monto)));
      var prop = el('input');
      prop.inputMode = 'numeric';
      prop.value = propina;
      prop.title = 'Propina';
      prop.setAttribute('aria-label', 'Propina de ' + p.etiqueta);
      prop.addEventListener('change', function () { p.propina = numero(prop.value); pintarDivision(); });
      li.appendChild(el('small', 'muted', '+ propina'));
      li.appendChild(prop);
      var metodo = el('select');
      metodo.setAttribute('aria-label', 'Método de pago de ' + p.etiqueta);
      [['efectivo', 'Efectivo'], ['nequi', 'Nequi / transferencia'], ['tarjeta', 'Tarjeta'], ['mixto', 'Mixto']].forEach(function (m) {
        var o = el('option', '', m[1]);
        o.value = m[0];
        o.selected = p.metodo === m[0];
        metodo.appendChild(o);
      });
      metodo.addEventListener('change', function () { p.metodo = metodo.value; pintarDivision(); });
      li.appendChild(metodo);
      if (p.metodo === 'mixto') {
        // Lo que da en efectivo; el resto de su parte va por transferencia.
        var efe = el('input');
        efe.inputMode = 'numeric';
        efe.value = p.efectivo || '';
        efe.placeholder = 'Efectivo';
        efe.setAttribute('aria-label', 'Efectivo de ' + p.etiqueta);
        efe.addEventListener('change', function () { p.efectivo = numero(efe.value); pintarDivision(); });
        li.appendChild(efe);
        li.appendChild(el('small', 'muted', 'transf. ' + Chef.pesos(Math.max(0, monto + propina - (p.efectivo || 0)))));
      }
      li.appendChild(el('strong', '', '= ' + Chef.pesos(monto + propina)));
      if (division.partes.length > 2 && division.modo === 'items') {
        li.appendChild(boton('Quitar', 'btn-link btn-link--peligro', function () {
          division.partes.splice(idx, 1);
          pintarDivision();
        }));
      }
      lista.appendChild(li);
    });
    $('div-apagar').textContent = Chef.pesos(aPagar);
    var falta = division.modo === 'items' && lineasDivisibles().some(function (i) { return redondear(i.cantidad - repartido(i.id_item)) !== 0; });
    var vacia = division.modo === 'items' && montos.some(function (m, idx) { return !Object.keys(division.partes[idx].items).length; });
    $('div-cobrar').disabled = falta || vacia;
    $('div-cobrar').textContent = falta ? 'Falta repartir' : (vacia ? 'Hay una cuenta vacía' : 'Cobrar las cuentas');
  }

  function cambiarPersonas(delta) {
    var n = Math.max(2, Math.min(20, division.partes.length + delta));
    while (division.partes.length < n) division.partes.push(nuevaParte(division.partes.length + 1));
    division.partes.length = n;
    pintarDivision();
  }

  function cobrarDividido() {
    var montos = montosPartes();
    var cuerpo = {
      modo: division.modo,
      uuid: Chef.uuid(),
      id_pedido: detalle.pedido.id_pedido,
      partes: division.partes.map(function (p, idx) {
        var parte = { etiqueta: p.etiqueta, metodo: p.metodo, propina: propinaDe(p) };
        if (p.metodo === 'mixto') parte.monto_efectivo = p.efectivo || 0;
        if (division.modo === 'items') {
          parte.items = Object.keys(p.items).map(function (id) { return { id_item: Number(id), cantidad: p.items[id] }; });
        }
        return parte;
      })
    };
    Chef.enviar('POST', API + '/cobrar-dividido', cuerpo, { etiqueta: etiqueta('cobro dividido'), mesa: COLA }).then(function (datos) {
      if (datos.ok) division = null;
      alCobrar(datos);
    });
  }

  function imprimirDivision() {
    var montos = montosPartes();
    imprimir(function (zona) {
      division.partes.forEach(function (p, idx) {
        var t = el('section', 'ticket');
        t.appendChild(el('h2', '', 'Precuenta · ' + titulo()));
        t.appendChild(el('p', '', p.etiqueta + ' · documento sin valor fiscal'));
        if (division.modo === 'items') {
          lineasDivisibles().forEach(function (i) {
            var cant = p.items[i.id_item];
            if (cant) t.appendChild(el('p', 'ticket__linea', cant + ' × ' + i.nombre + '  ' + Chef.pesos(cant * i.precio_unitario)));
          });
        } else {
          t.appendChild(el('p', '', 'Parte ' + (idx + 1) + ' de ' + division.partes.length + ' de ' + Chef.pesos(detalle.total)));
        }
        var propina = propinaDe(p);
        t.appendChild(el('p', 'ticket__total', 'Total ' + Chef.pesos(montos[idx])));
        t.appendChild(el('p', '', 'Propina (voluntaria) ' + Chef.pesos(propina)));
        t.appendChild(el('p', 'ticket__total', 'A pagar ' + Chef.pesos(montos[idx] + propina)));
        zona.appendChild(t);
      });
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
    Chef.api('POST', API + '/mover', { id_mesa_destino: destino }).then(function (datos) {
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
      ? (llevar ? 'El pedido se cancela sin cobrar.' : 'La mesa queda libre sin cobrar.') + ' Solo se puede si nada fue a cocina.'
      : (enviado ? 'Ya fue a cocina. Si se cocinó, queda como pérdida (merma).' : 'Aún no fue a cocina: se quita sin costo.');
    $('bloque-devolver').hidden = !enviado;
    $('anular-devolver').checked = false;
    $('anular-motivo').value = '';
    $('anular-motivo').required = !item || enviado;
    $('dlg-anular').showModal();
  }

  function anular() {
    var cuerpo = { motivo: $('anular-motivo').value.trim(), devolver: $('anular-devolver').checked };
    var url = anulando ? '/api/pedido-items/' + anulando.id_item + '/anular' : API + '/anular';
    Chef.api('POST', url, cuerpo).then(function (datos) {
      if (!datos.ok) { Chef.mostrar(datos.msg, true); return; }
      Chef.mostrar(datos.msg, false);
      if (!anulando) { window.location.href = esDomicilio() ? '/domicilios' : '/mesas'; return; }
      cargar();
    }, function () { Chef.mostrar('Para anular se necesita conexión.', true); });
  }

  function entregar() {
    Chef.enviar('POST', API + '/entregar', null, { etiqueta: etiqueta('entregado'), mesa: COLA }).then(function (datos) {
      if (datos.offline) { Chef.mostrar('Sin conexión: quedó guardado y se marca al volver internet.', false); return; }
      if (!datos.ok) { Chef.mostrar(datos.msg, true); return; }
      Chef.mostrar(datos.msg, false);
      window.setTimeout(function () { window.location.href = '/mesas'; }, 700);
    });
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
  $('btn-entregar').addEventListener('click', entregar);
  $('btn-dividir').addEventListener('click', abrirDividir);
  $('div-menos').addEventListener('click', function () { cambiarPersonas(-1); });
  $('div-mas').addEventListener('click', function () { cambiarPersonas(1); });
  $('div-imprimir').addEventListener('click', imprimirDivision);
  document.querySelectorAll('#form-dividir [data-modo]').forEach(function (b) {
    b.addEventListener('click', function () { division.modo = b.dataset.modo; pintarDivision(); });
  });
  if (llevar) {
    ['cliente-nombre', 'cliente-telefono', 'cliente-direccion'].forEach(function (id) {
      $(id).addEventListener('change', function () {
        Chef.guardar(CLAVE_CLIENTE, { cliente: $('cliente-nombre').value.trim(), telefono: $('cliente-telefono').value.trim(),
          direccion: $('cliente-direccion').value.trim() });
      });
    });
  }
  ['cobro-propina', 'cobro-metodo', 'cobro-efectivo', 'cobro-recibido'].forEach(function (id) {
    $(id).addEventListener('input', recalcularCobro);
  });
  $('propina-sugerida').addEventListener('click', function () { $('cobro-propina').value = detalle.propina_sugerida; recalcularCobro(); });
  $('propina-cero').addEventListener('click', function () { $('cobro-propina').value = 0; recalcularCobro(); });
  alCerrar('dlg-cobro', cobrar);
  alCerrar('dlg-mover', mover);
  alCerrar('dlg-dividir', cobrarDividido);
  alCerrar('dlg-anular', anular);
  document.addEventListener('chef:sincronizado', cargar);

  pintarCarta();
  pintarCliente();
  pintarCuenta();
  cargarCarta();
  cargar();
})();
