/* Plano de mesas: una tarjeta por mesa con su estado, total y avisos, y
 * al lado (debajo en celular) los pedidos para llevar sin entregar. Se refresca cada 10 s. Sin
 * conexion muestra el ultimo plano guardado. */
(function () {
  'use strict';

  var CLAVE = 'chef_plano_v1';
  var zonaActiva = Chef.leer('chef_zona_v1', 'todas');
  var ESTADOS = { libre: 'Libre', abierto: 'Ocupada', por_cobrar: 'Pide la cuenta' };

  function el(tag, clase, texto) {
    var n = document.createElement(tag);
    if (clase) n.className = clase;
    if (texto !== undefined) n.textContent = texto;
    return n;
  }

  function pintarZonas(datos) {
    var nav = document.getElementById('zonas');
    nav.textContent = '';
    if (!datos.zonas.length) { nav.hidden = true; return; }
    nav.hidden = false;
    var opciones = [{ id_zona: 'todas', nombre: 'Todas' }].concat(datos.zonas);
    if (zonaActiva !== 'todas' && !datos.zonas.some(function (z) { return String(z.id_zona) === String(zonaActiva); })) zonaActiva = 'todas';
    opciones.forEach(function (z) {
      var b = el('button', 'pestana' + (String(z.id_zona) === String(zonaActiva) ? ' pestana--activa' : ''), z.nombre);
      b.type = 'button';
      b.addEventListener('click', function () {
        zonaActiva = z.id_zona;
        Chef.guardar('chef_zona_v1', zonaActiva);
        pintar(datos);
      });
      nav.appendChild(b);
    });
  }

  function primerNombre(nombre) { return (nombre || '').split(' ')[0]; }

  // Mas de una hora con la mesa abierta: el tiempo se resalta para que el
  // mesero pase a revisar.
  var MINUTOS_ALERTA = 60;

  function meta(texto, minutos) {
    return el('small', 'mesa__meta' + (minutos >= MINUTOS_ALERTA ? ' mesa__meta--alerta' : ''), texto);
  }

  function pintar(datos) {
    pintarZonas(datos);
    var plano = document.getElementById('plano');
    plano.textContent = '';
    document.getElementById('sin-mesas').hidden = datos.mesas.length > 0;
    var conteo = { libre: 0, abierto: 0, por_cobrar: 0 };
    datos.mesas.forEach(function (m) {
      conteo[m.estado] = (conteo[m.estado] || 0) + 1;
      if (zonaActiva !== 'todas' && String(m.id_zona) !== String(zonaActiva)) return;
      var estado = ESTADOS[m.estado] || m.estado;
      var a = el('a', 'mesa mesa--' + m.estado);
      a.href = '/mesas/' + m.id_mesa;
      var cab = el('span', 'mesa__cab');
      cab.appendChild(el('strong', 'mesa__nombre', m.nombre));
      cab.appendChild(el('span', 'mesa__estado', estado));
      a.appendChild(cab);
      if (m.estado !== 'libre') {
        a.appendChild(el('span', 'mesa__total', Chef.pesos(m.total)));
        a.appendChild(meta(primerNombre(m.mesero) + ' · ' + m.minutos + ' min', m.minutos));
        a.setAttribute('aria-label', m.nombre + ', ' + estado + ', ' + Chef.pesos(m.total));
      } else {
        a.appendChild(el('span', 'mesa__accion', 'Toca para abrir'));
        a.appendChild(el('small', 'mesa__meta', m.capacidad + ' puesto' + (m.capacidad === 1 ? '' : 's')));
        a.setAttribute('aria-label', m.nombre + ', libre, ' + m.capacidad + ' puestos');
      }
      if (m.listos) a.appendChild(el('span', 'insignia insignia--ok', m.listos + (m.listos === 1 ? ' plato listo para servir' : ' platos listos para servir')));
      if (m.sin_enviar) a.appendChild(el('span', 'insignia', m.sin_enviar + ' sin enviar a cocina'));
      if (Chef.pendientesDeMesa(m.id_mesa).length) a.appendChild(el('span', 'insignia insignia--offline', 'Guardado sin conexión'));
      plano.appendChild(a);
    });
    Object.keys(conteo).forEach(function (k) {
      var n = document.querySelector('[data-conteo="' + k + '"]');
      if (n) n.textContent = conteo[k];
    });
    pintarLlevar(datos.llevar || []);
    var total = datos.mesas.length;
    var libres = conteo.libre;
    document.getElementById('resumen-mesas').textContent =
      (total ? libres + (libres === 1 ? ' mesa libre' : ' mesas libres') + ' de ' + total : 'Sin mesas') +
      (navigator.onLine ? '' : ' · sin conexión, mostrando lo último guardado');
  }

  var ESTADOS_LLEVAR = { abierto: 'Tomando pedido', por_cobrar: 'Por cobrar', cerrado: 'Pagado, falta entregar' };

  function pintarLlevar(lista) {
    var cont = document.getElementById('llevar');
    cont.textContent = '';
    document.getElementById('sin-llevar').hidden = lista.length > 0;
    document.getElementById('cuenta-llevar').textContent = lista.length;
    lista.forEach(function (p) {
      var estado = ESTADOS_LLEVAR[p.estado] || p.estado;
      var a = el('a', 'mesa mesa--fila mesa--' + p.estado);
      a.href = '/llevar/' + p.uuid;
      var cab = el('span', 'mesa__cab');
      cab.appendChild(el('strong', 'mesa__nombre', '#' + p.numero + (p.cliente ? ' · ' + p.cliente : '')));
      cab.appendChild(el('span', 'mesa__estado', estado));
      a.appendChild(cab);
      var pie = el('span', 'mesa__pie');
      pie.appendChild(el('span', 'mesa__total', Chef.pesos(p.total)));
      pie.appendChild(meta(primerNombre(p.mesero) + ' · ' + p.minutos + ' min', p.minutos));
      a.appendChild(pie);
      a.setAttribute('aria-label', 'Para llevar número ' + p.numero + (p.cliente ? ' de ' + p.cliente : '') + ', ' + estado + ', ' + Chef.pesos(p.total));
      if (p.listos) a.appendChild(el('span', 'insignia insignia--ok', 'Listo para entregar'));
      else if (p.en_cocina) a.appendChild(el('span', 'insignia insignia--cocina', 'Preparándose en cocina'));
      if (p.sin_enviar) a.appendChild(el('span', 'insignia', p.sin_enviar + ' sin enviar a cocina'));
      if (Chef.pendientesDeMesa('llevar:' + p.uuid).length) a.appendChild(el('span', 'insignia insignia--offline', 'Guardado sin conexión'));
      cont.appendChild(a);
    });
  }

  document.getElementById('btn-nuevo-llevar').addEventListener('click', function () {
    window.location.href = '/llevar/' + Chef.uuid();
  });

  function cargar() {
    Chef.api('GET', '/api/mesas/plano').then(function (datos) {
      if (!datos.ok) { Chef.mostrar(datos.msg || 'No se pudo cargar el plano.', true); return; }
      Chef.guardar(CLAVE, datos);
      pintar(datos);
    }, function () {
      var guardado = Chef.leer(CLAVE, null);
      if (guardado) pintar(guardado);
    });
  }

  document.addEventListener('chef:sincronizado', cargar);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) cargar(); });
  cargar();
  window.setInterval(function () { if (!document.hidden) cargar(); }, 10000);
})();
