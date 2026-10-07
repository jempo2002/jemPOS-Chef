/* jemPOS Chef: editor de receta con costo en vivo y vista previa del costo
 * de una compra.
 *
 * El editor viene del de jemPOS (static/js/inventario.js: addRecipeRow,
 * getRecipeTotal), movido a su propia pantalla. Las filas son insumo +
 * cantidad en la unidad base del insumo (g, ml, und); el costo se calcula
 * con el costo por unidad que trae la pagina. Guardar reemplaza la receta
 * completa (PUT /api/recetas/<id>).
 */
(function () {
  'use strict';

  var csrf = document.querySelector('meta[name="csrf-token"]');

  function pesos(valor) {
    return '$' + Math.round(Number(valor) || 0).toString().replace(/\B(?=(\d{3})+(?!\d))/g, '.');
  }

  function numero(texto) {
    // Igual que el servidor: "1,5" kg, "2.000" g (punto de miles), "1.5".
    var t = String(texto || '').trim().replace(/[$\s]/g, '');
    if (t.indexOf(',') !== -1 || /^\d{1,3}(\.\d{3})+$/.test(t)) t = t.replace(/\./g, '');
    return Number(t.replace(',', '.')) || 0;
  }

  function entero(texto) {
    // Pesos con puntos de miles: "20.000".
    return Number(String(texto || '').replace(/\D/g, '')) || 0;
  }

  function mostrar(msg, error) {
    var caja = document.getElementById('mensaje');
    if (!caja) { window.alert(msg); return; }
    caja.textContent = msg;
    caja.className = 'aviso aviso--' + (error ? 'error' : 'ok');
    caja.hidden = false;
    caja.scrollIntoView({ block: 'nearest' });
  }

  /* --- editor de receta -------------------------------------------------- */

  var editor = document.getElementById('editor-receta');
  if (editor) iniciarEditor(editor);

  function iniciarEditor(raiz) {
    var insumos = JSON.parse(raiz.dataset.insumos || '[]');
    var lineas = JSON.parse(raiz.dataset.lineas || '[]');
    var precio = Number(raiz.dataset.precio) || 0;
    var margen = Number(raiz.dataset.margen) || 35;
    var filas = document.getElementById('receta-filas');
    var porId = {};
    insumos.forEach(function (i) { porId[i.id_insumo] = i; });

    function opciones(select, elegido) {
      var vacia = document.createElement('option');
      vacia.value = '';
      vacia.textContent = 'Elige un insumo';
      select.appendChild(vacia);
      insumos.forEach(function (i) {
        var o = document.createElement('option');
        o.value = i.id_insumo;
        o.textContent = i.nombre + ' (' + i.abreviatura + ')';
        if (String(i.id_insumo) === String(elegido)) o.selected = true;
        select.appendChild(o);
      });
    }

    function agregarFila(linea) {
      var fila = document.createElement('div');
      fila.className = 'receta-fila';
      var select = document.createElement('select');
      select.setAttribute('aria-label', 'Insumo');
      opciones(select, linea ? linea.id_insumo : '');
      var cantidad = document.createElement('input');
      cantidad.inputMode = 'decimal';
      cantidad.placeholder = 'Cantidad';
      cantidad.setAttribute('aria-label', 'Cantidad');
      cantidad.value = linea ? String(linea.cantidad).replace('.', ',') : '';
      var unidad = document.createElement('span');
      unidad.className = 'muted receta-unidad';
      var costo = document.createElement('span');
      costo.className = 'receta-costo-linea';
      var quitar = document.createElement('button');
      quitar.type = 'button';
      quitar.className = 'btn btn--mini';
      quitar.textContent = '×';
      quitar.setAttribute('aria-label', 'Quitar ingrediente');
      quitar.addEventListener('click', function () {
        fila.remove();
        if (!filas.children.length) agregarFila(null);
        recalcular();
      });
      select.addEventListener('change', recalcular);
      cantidad.addEventListener('input', recalcular);
      [select, cantidad, unidad, costo, quitar].forEach(function (n) { fila.appendChild(n); });
      filas.appendChild(fila);
    }

    function leerFilas() {
      return Array.prototype.map.call(filas.querySelectorAll('.receta-fila'), function (fila) {
        var id = fila.querySelector('select').value;
        return { fila: fila, insumo: porId[id], id_insumo: Number(id), cantidad: numero(fila.querySelector('input').value) };
      });
    }

    function recalcular() {
      var total = 0;
      leerFilas().forEach(function (l) {
        var unidad = l.fila.querySelector('.receta-unidad');
        var costo = l.fila.querySelector('.receta-costo-linea');
        unidad.textContent = l.insumo ? l.insumo.abreviatura : '';
        var valor = l.insumo && l.cantidad > 0 ? l.cantidad * l.insumo.costo_unitario : 0;
        costo.textContent = valor ? pesos(valor) : '';
        total += valor;
      });
      document.getElementById('receta-costo').textContent = pesos(total);
      document.getElementById('receta-utilidad').textContent = pesos(precio - total);
      var pct = precio > 0 ? (total * 100 / precio) : 0;
      document.getElementById('receta-pct').textContent = (Math.round(pct * 10) / 10).toString().replace('.', ',') + ' %';
      document.getElementById('receta-pct-fila').classList.toggle('texto-peligro', pct > margen);
    }

    document.getElementById('receta-agregar').addEventListener('click', function () { agregarFila(null); });
    document.getElementById('receta-guardar').addEventListener('click', function (ev) {
      var boton = ev.currentTarget;
      var datos = leerFilas().filter(function (l) { return l.id_insumo > 0 && l.cantidad > 0; })
        .map(function (l) { return { id_insumo: l.id_insumo, cantidad: l.cantidad }; });
      if (!datos.length && !window.confirm('La receta queda vacía y el plato dejará de descontar ingredientes. ¿Seguir?')) return;
      var partes = raiz.dataset.api.split(' ');
      boton.disabled = true;
      fetch(partes[1], {
        method: partes[0],
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json', 'X-CSRFToken': csrf ? csrf.content : '' },
        body: JSON.stringify({ lineas: datos })
      })
        .then(function (r) {
          if (r.status === 401) { window.location.href = '/login'; return null; }
          return r.json().catch(function () { return { ok: false, msg: 'Error ' + r.status }; });
        })
        .then(function (data) {
          if (!data) return;
          mostrar(data.msg || (data.ok ? 'Listo.' : 'No se pudo guardar.'), !data.ok);
        })
        .catch(function () { mostrar('Sin conexión. Intenta de nuevo.', true); })
        .finally(function () { boton.disabled = false; });
    });

    if (lineas.length) lineas.forEach(agregarFila); else agregarFila(null);
    recalcular();
  }

  /* --- vista previa del costo de una compra -------------------------------- */

  document.addEventListener('input', function (ev) {
    var form = ev.target.closest && ev.target.closest('form[data-costo-base]');
    if (form) previa(form);
  });
  document.addEventListener('change', function (ev) {
    var form = ev.target.closest && ev.target.closest('form[data-costo-base]');
    if (form) previa(form);
  });

  function previa(form) {
    var salida = form.querySelector('.vista-costo');
    var tipo = form.elements.tipo.value;
    var opcion = form.elements.unidad.selectedOptions[0];
    var base = numero(form.elements.cantidad.value) * (Number(opcion && opcion.dataset.factor) || 1);
    var precio = entero(form.elements.precio_total.value);
    form.elements.precio_total.hidden = tipo !== 'Entrada';
    if (tipo === 'Entrada' && base > 0 && precio > 0) {
      var porUnidad = precio / base;
      salida.textContent = 'Sale a $' + (Math.round(porUnidad * 100) / 100).toString().replace('.', ',') +
        ' por ' + form.dataset.costoBase + ' (' + (Math.round(base * 1000) / 1000).toString().replace('.', ',') + ' ' + form.dataset.costoBase + ')';
    } else if (base > 0 && opcion && opcion.dataset.factor !== '1') {
      salida.textContent = '= ' + (Math.round(base * 1000) / 1000).toString().replace('.', ',') + ' ' + form.dataset.costoBase;
    } else {
      salida.textContent = '';
    }
  }
})();
