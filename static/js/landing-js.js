/* Marca que hay JS antes de pintar: las animaciones de entrada solo
   esconden contenido si este script corrio. Va en archivo propio porque la
   CSP no admite scripts en linea. */
document.documentElement.classList.add('js');
