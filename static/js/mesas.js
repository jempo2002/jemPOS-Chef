/* Plano de mesas: una tarjeta por mesa con su estado, total y avisos.
 * Se refresca cada 10 s. Sin conexion muestra el ultimo plano guardado. */
(function () {
  'use strict';

  var CLAVE = 'chef_plano_v1';
  var zonaActiva = Chef.leer('chef_zona_v1', 'todas');
  var ESTADOS = { libre: 'Libre', abierto: 'Ocupada', por_cobrar: 'Pidió la cuenta' };

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

  function pintar(datos) {
    pintarZonas(datos);
    var plano = document.getElementById('plano');
    plano.textContent = '';
    document.getElementById('sin-mesas').hidden = datos.mesas.length > 0;
    var ocupadas = 0;
    datos.mesas.forEach(function (m) {
      if (m.estado !== 'libre') ocupadas += 1;
      if (zonaActiva !== 'todas' && String(m.id_zona) !== String(zonaActiva)) return;
      var a = el('a', 'mesa mesa--' + m.estado);
      a.href = '/mesas/' + m.id_mesa;
      a.appendChild(el('strong', 'mesa__nombre', m.nombre));
      a.appendChild(el('span', 'mesa__estado', ESTADOS[m.estado] || m.estado));
      if (m.estado !== 'libre') {
        a.appendChild(el('span', 'mesa__total', Chef.pesos(m.total)));
        a.appendChild(el('small', 'mesa__meta', (m.mesero || '').split(' ')[0] + ' · ' + m.minutos + ' min'));
      } else {
        a.appendChild(el('small', 'mesa__meta', m.capacidad + ' puestos'));
      }
      if (m.listos) a.appendChild(el('span', 'insignia insignia--ok', m.listos + ' listo' + (m.listos === 1 ? '' : 's')));
      if (m.sin_enviar) a.appendChild(el('span', 'insignia', m.sin_enviar + ' sin enviar'));
      if (Chef.pendientesDeMesa(m.id_mesa).length) a.appendChild(el('span', 'insignia insignia--offline', 'Guardado sin conexión'));
      plano.appendChild(a);
    });
    document.getElementById('resumen-mesas').textContent =
      ocupadas + ' de ' + datos.mesas.length + ' mesas ocupadas' + (navigator.onLine ? '' : ' · sin conexión, datos de antes');
  }

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
