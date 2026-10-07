-- ============================================================
-- Migracion: sedes, planes de Chef y roles de restaurante
-- Fecha: 2026-10-07
-- Motor: MariaDB y MySQL 8/9
-- ============================================================
--
-- Va encima de db/schema.sql (el esquema de jemPOS sin datos).
--
-- 1) sedes: cada local fisico de un restaurante. Toda tienda tiene al menos
--    una (la principal). Basico permite 1; Completo y Cadena hasta 4. Cada
--    sede extra paga el 50 % del plan al mes y un montaje unico de $79.000
--    (app/services/plan_service.py). costo_montaje guarda lo que se cobro al
--    crearla (0 en la principal) y montaje_pagado si el Master ya lo recibio.
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
-- 5) Triggers: la base rechaza (SQLSTATE 45000) una sede activa de mas
--    (maximo 4; 1 si el plan no es completo ni cadena) y bajar a un plan sin
--    multisede con varias sedes activas. La app valida antes con mensajes
--    claros; esto es el respaldo si algo escribe directo en la base. Los ids
--    de plan se repiten aqui: si cambian en plan_service.py, cambian aqui.
--
-- 6) Relleno: cada tienda sin sede recibe una "Principal", y sus usuarios que
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
  `costo_montaje` decimal(12,0) NOT NULL DEFAULT 0,
  `montaje_pagado` tinyint(1) NOT NULL DEFAULT 0,
  `fecha_pago_montaje` datetime DEFAULT NULL,
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

DROP TRIGGER IF EXISTS `bi_sedes_limite`;
DROP TRIGGER IF EXISTS `bu_sedes_limite`;
DROP TRIGGER IF EXISTS `bu_tiendas_plan_sedes`;

DELIMITER $$
CREATE TRIGGER `bi_sedes_limite` BEFORE INSERT ON `sedes` FOR EACH ROW
BEGIN
  DECLARE v_plan varchar(20);
  DECLARE v_activas int;
  IF NEW.`estado` = 'Activa' THEN
    SELECT `plan_id` INTO v_plan FROM `tiendas` WHERE `id_tienda` = NEW.`id_tienda`;
    SELECT COUNT(*) INTO v_activas FROM `sedes` WHERE `id_tienda` = NEW.`id_tienda` AND `estado` = 'Activa';
    IF v_activas >= IF(v_plan IN ('completo', 'cadena'), 4, 1) THEN
      SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Limite de sedes del plan alcanzado';
    END IF;
  END IF;
END$$

CREATE TRIGGER `bu_sedes_limite` BEFORE UPDATE ON `sedes` FOR EACH ROW
BEGIN
  DECLARE v_plan varchar(20);
  DECLARE v_activas int;
  IF NEW.`estado` = 'Activa' AND (OLD.`estado` <> 'Activa' OR NEW.`id_tienda` <> OLD.`id_tienda`) THEN
    SELECT `plan_id` INTO v_plan FROM `tiendas` WHERE `id_tienda` = NEW.`id_tienda`;
    SELECT COUNT(*) INTO v_activas FROM `sedes`
      WHERE `id_tienda` = NEW.`id_tienda` AND `estado` = 'Activa' AND `id_sede` <> NEW.`id_sede`;
    IF v_activas >= IF(v_plan IN ('completo', 'cadena'), 4, 1) THEN
      SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Limite de sedes del plan alcanzado';
    END IF;
  END IF;
END$$

CREATE TRIGGER `bu_tiendas_plan_sedes` BEFORE UPDATE ON `tiendas` FOR EACH ROW
BEGIN
  IF NOT (NEW.`plan_id` <=> OLD.`plan_id`) AND COALESCE(NEW.`plan_id`, '') NOT IN ('completo', 'cadena')
     AND (SELECT COUNT(*) FROM `sedes` WHERE `id_tienda` = NEW.`id_tienda` AND `estado` = 'Activa') > 1 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'El plan no admite varias sedes';
  END IF;
END$$
DELIMITER ;
