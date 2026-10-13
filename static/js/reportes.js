/* Reportes: el periodo "Elegir fechas" muestra Desde/Hasta; los demas
 * filtros se aplican apenas se eligen. */
(function () {
  'use strict';

  var periodo = document.getElementById('filtro-periodo');
  var rango = document.getElementById('filtro-rango');
  if (periodo && rango) {
    periodo.addEventListener('change', function () {
      rango.hidden = periodo.value !== 'rango';
      if (periodo.value !== 'rango') periodo.form.submit();
    });
  }
  var sede = document.querySelector('#filtros-reportes select[name="sede"]');
  if (sede) sede.addEventListener('change', function () { sede.form.submit(); });

  document.querySelectorAll('select.enviar-al-cambiar').forEach(function (sel) {
    sel.addEventListener('change', function () { sel.form.submit(); });
  });
})();
