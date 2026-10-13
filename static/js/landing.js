/* ============================================================
   jemPOS Chef: landing. Vanilla JS, sin dependencias.
     1. Header con sombra al hacer scroll
     2. Menu movil accesible
     3. Entrada escalonada de tarjetas (IntersectionObserver)
     4. Conteo de cifras ([data-contar])
     5. Mesas del hero: la mesa activa cambia de estado sola
   Solo se anima opacity/transform. Con prefers-reduced-motion o sin
   IntersectionObserver todo se muestra de una.
   ============================================================ */
(() => {
  'use strict';

  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

  const initHeader = () => {
    const header = document.querySelector('[data-header]');
    if (!header) return;
    let ticking = false;
    const update = () => {
      header.classList.toggle('is-scrolled', window.scrollY > 8);
      ticking = false;
    };
    window.addEventListener('scroll', () => {
      if (!ticking) {
        ticking = true;
        requestAnimationFrame(update);
      }
    }, { passive: true });
    update();
  };

  const initMobileNav = () => {
    const toggle = document.querySelector('[data-nav-toggle]');
    const menu = document.querySelector('[data-nav-menu]');
    if (!toggle || !menu) return;

    const setOpen = (open) => {
      toggle.setAttribute('aria-expanded', String(open));
      menu.classList.toggle('is-open', open);
      toggle.querySelector('.visually-hidden').textContent = open ? 'Cerrar menú' : 'Abrir menú';
    };

    toggle.addEventListener('click', () => setOpen(toggle.getAttribute('aria-expanded') !== 'true'));
    menu.addEventListener('click', (e) => { if (e.target.closest('a')) setOpen(false); });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') setOpen(false); });
    document.addEventListener('click', (e) => {
      const abierto = toggle.getAttribute('aria-expanded') === 'true';
      if (abierto && !menu.contains(e.target) && !toggle.contains(e.target)) setOpen(false);
    });
  };

  const initReveal = () => {
    const elements = document.querySelectorAll('.reveal');
    if (!elements.length) return;

    if (reducedMotion.matches || !('IntersectionObserver' in window)) {
      elements.forEach((el) => el.classList.add('is-visible'));
      return;
    }

    document.querySelectorAll('[data-reveal-grupo]').forEach((grupo) => {
      grupo.querySelectorAll('.reveal').forEach((el, i) => {
        el.style.setProperty('--i', String(Math.min(i, 5)));
      });
    });

    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        const el = entry.target;
        el.classList.add('is-visible');
        observer.unobserve(el);
        setTimeout(() => el.style.removeProperty('--i'), 1100);
      });
    }, { threshold: 0.12, rootMargin: '0px 0px -5% 0px' });

    elements.forEach((el) => observer.observe(el));
  };

  /* El HTML ya trae la cifra final: sin JS o con movimiento reducido no cambia nada. */
  const initContar = () => {
    const cifras = document.querySelectorAll('[data-contar]');
    if (!cifras.length || reducedMotion.matches || !('IntersectionObserver' in window)) return;

    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        const el = entry.target;
        const total = Number(el.dataset.contar);
        const prefijo = el.dataset.prefijo || '';
        const sufijo = el.dataset.sufijo || '';
        observer.unobserve(el);
        if (!total) return;
        const inicio = performance.now();
        const paso = (ahora) => {
          const t = Math.min((ahora - inicio) / 900, 1);
          const valor = Math.round(total * (1 - Math.pow(1 - t, 3)));
          el.textContent = prefijo + valor.toLocaleString('es-CO') + sufijo;
          if (t < 1) requestAnimationFrame(paso);
        };
        requestAnimationFrame(paso);
      });
    }, { threshold: 0.6 });

    cifras.forEach((el) => observer.observe(el));
  };

  /* Mesa 4 pasa de "ocupada" a "pide la cuenta" y vuelve, para que el hero
     muestre el flujo sin que nadie toque nada. */
  const initMesaViva = () => {
    const mesa = document.querySelector('[data-mesa-viva]');
    if (!mesa || reducedMotion.matches) return;
    const estado = mesa.querySelector('[data-mesa-estado]');
    let cuenta = false;
    setInterval(() => {
      cuenta = !cuenta;
      mesa.classList.toggle('mesa--cuenta', cuenta);
      mesa.classList.toggle('mesa--ocupada', !cuenta);
      if (estado) estado.textContent = cuenta ? 'Cuenta' : 'Comiendo';
    }, 3600);
  };

  document.addEventListener('DOMContentLoaded', () => {
    initHeader();
    initMobileNav();
    initReveal();
    initContar();
    initMesaViva();
  });
})();
