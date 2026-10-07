-- ============================================================
-- Migracion: sedes, planes de Chef y roles de restaurante
-- Fecha: 2026-10-07
-- Motor: MariaDB y MySQL 8/9
-- ============================================================
--
-- Va encima de db/schema.sql (el esquema de jemPOS sin datos).
--
-- 1) sedes: cada local fisico de un restaurante. Toda tienda tiene al menos
--    una (la principal). Basico y Completo permiten 1; Cadena las que quiera,
--    y cada una por encima de la incluida se cobra aparte
--    (app/services/plan_service.py).
--    Soft delete: estado = 'Eliminada' + fecha_eliminacion. La fila no se
--    borra porque ventas, turnos y movimientos de inventario la van a
--    referenciar. `nombre_vivo` es NULL en las eliminadas: el UNIQUE solo
--    impide dos sedes vivas con el mismo nombre en un restaurante y deja
--    reusar el nombre de una eliminada (MySQL no tiene indices parciales).
--
-- 2) usuarios.id_sede: la sede donde trabaja. NULL = todas (lo normal en un
--    Admin; elige sede al entrar). Cajero, Mesero y Cocina siempre tienen una.
--
-- 3) usuarios.rol: se suman Mesero y Cocina (plan de mesas y comandas).
--
-- 4) tiendas.plan_id sigue siendo texto: 'basico', 'completo' o 'cadena'.
--
-- 5) Relleno: cada tienda sin sede recibe una "Principal", y sus usuarios que
--    no son Admin quedan en ella.
--
-- Se puede correr varias veces.

CREATE TABLE IF NOT EXISTS `sedes` (
  `id_sede` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `id_tienda` bigint(20) UNSIGNED NOT NULL,
  `nombre` varchar(120) NOT NULL,
  `direccion` varchar(200) DEFAULT NULL,
  `telefono` varchar(20) DEFAULT NULL,
  `es_principal` tinyint(1) NOT NULL DEFAULT 0,
  `estado` enum('Activa','Eliminada') NOT NULL DEFAULT 'Activa',
  `nombre_vivo` varchar(120) GENERATED ALWAYS AS (IF(`estado` = 'Activa', `nombre`, NULL)) STORED,
  `fecha_creacion` timestamp NOT NULL DEFAULT current_timestamp(),
  `fecha_eliminacion` datetime DEFAULT NULL,
  PRIMARY KEY (`id_sede`),
  UNIQUE KEY `uq_sedes_nombre_vivo` (`id_tienda`, `nombre_vivo`),
  KEY `idx_sedes_tienda_estado` (`id_tienda`, `estado`),
  CONSTRAINT `fk_sedes_tiendas` FOREIGN KEY (`id_tienda`) REFERENCES `tiendas` (`id_tienda`) ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

SET @s := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'usuarios' AND COLUMN_NAME = 'id_sede'), 'DO 0', 'ALTER TABLE `usuarios` ADD COLUMN `id_sede` bigint(20) UNSIGNED DEFAULT NULL AFTER `id_tienda`, ADD KEY `idx_usuarios_sede` (`id_sede`), ADD CONSTRAINT `fk_usuarios_sedes` FOREIGN KEY (`id_sede`) REFERENCES `sedes` (`id_sede`) ON DELETE SET NULL ON UPDATE CASCADE');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;

-- MODIFY es idempotente: dejar el enum con los cinco valores siempre.
ALTER TABLE `usuarios` MODIFY `rol` enum('Master','Admin','Cajero','Mesero','Cocina') NOT NULL DEFAULT 'Cajero';

INSERT INTO `sedes` (`id_tienda`, `nombre`, `telefono`, `es_principal`)
SELECT t.`id_tienda`, 'Principal', t.`telefono`, 1
FROM `tiendas` t
WHERE NOT EXISTS (SELECT 1 FROM `sedes` s WHERE s.`id_tienda` = t.`id_tienda`);

UPDATE `usuarios` u
JOIN `sedes` s ON s.`id_tienda` = u.`id_tienda` AND s.`es_principal` = 1 AND s.`estado` = 'Activa'
SET u.`id_sede` = s.`id_sede`
WHERE u.`id_sede` IS NULL AND u.`rol` NOT IN ('Master', 'Admin');
