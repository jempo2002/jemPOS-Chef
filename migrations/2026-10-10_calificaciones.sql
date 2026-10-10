-- ============================================================
-- Migracion: calificacion del servicio
-- Fecha: 2026-10-10
-- Motor: MariaDB y MySQL 8/9
-- ============================================================
--
-- Va despues de 2026-10-09_contabilidad_reportes.sql. Se puede correr varias
-- veces.
--
-- 1) calificaciones: una por pedido (mesa, para llevar o domicilio; una
--    cuenta dividida es un solo pedido, asi que se califica una vez). La fila
--    nace cuando el equipo pide el QR despues de cobrar, con un token al azar
--    que va en el enlace: el cliente entra sin cuenta y califica de 1 a 5 la
--    comida y la atencion, con un comentario opcional. Una sola vez por
--    pedido y solo durante unos dias (calificaciones_service.VIGENCIA_DIAS).
--    id_mesero se copia del pedido para los promedios por mesero.
--    El Admin puede ocultar una calificacion (prueba, insulto, spam) con
--    motivo: deja de contar en los promedios pero nunca se borra.
-- 2) tiendas.pedir_calificacion: mostrar el QR al cobrar (1) o no (0). Se
--    cambia en Reportes > Calificaciones.

CREATE TABLE IF NOT EXISTS `calificaciones` (
  `id_calificacion` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `id_tienda` bigint(20) UNSIGNED NOT NULL,
  `id_sede` bigint(20) UNSIGNED NOT NULL,
  `id_pedido` bigint(20) UNSIGNED NOT NULL,
  `id_mesero` bigint(20) UNSIGNED DEFAULT NULL,
  `token` char(32) NOT NULL,
  `comida` tinyint(3) UNSIGNED DEFAULT NULL,
  `atencion` tinyint(3) UNSIGNED DEFAULT NULL,
  `comentario` varchar(500) DEFAULT NULL,
  `creada_en` timestamp NOT NULL DEFAULT current_timestamp(),
  `calificada_en` datetime DEFAULT NULL,
  `estado_activo` tinyint(1) NOT NULL DEFAULT 1,
  `oculta_por` bigint(20) UNSIGNED DEFAULT NULL,
  `oculta_en` datetime DEFAULT NULL,
  `motivo_oculta` varchar(255) DEFAULT NULL,
  PRIMARY KEY (`id_calificacion`),
  UNIQUE KEY `uq_calificaciones_pedido` (`id_pedido`),
  UNIQUE KEY `uq_calificaciones_token` (`token`),
  KEY `idx_calificaciones_tienda_fecha` (`id_tienda`, `calificada_en`),
  KEY `idx_calificaciones_sede_fecha` (`id_sede`, `calificada_en`),
  KEY `idx_calificaciones_mesero` (`id_mesero`),
  CONSTRAINT `fk_calificaciones_tiendas` FOREIGN KEY (`id_tienda`) REFERENCES `tiendas` (`id_tienda`) ON UPDATE CASCADE,
  CONSTRAINT `fk_calificaciones_sedes` FOREIGN KEY (`id_sede`) REFERENCES `sedes` (`id_sede`) ON UPDATE CASCADE,
  CONSTRAINT `fk_calificaciones_pedidos` FOREIGN KEY (`id_pedido`) REFERENCES `pedidos` (`id_pedido`) ON UPDATE CASCADE,
  CONSTRAINT `fk_calificaciones_mesero` FOREIGN KEY (`id_mesero`) REFERENCES `usuarios` (`id_usuario`) ON UPDATE CASCADE,
  CONSTRAINT `fk_calificaciones_oculta_por` FOREIGN KEY (`oculta_por`) REFERENCES `usuarios` (`id_usuario`) ON UPDATE CASCADE,
  CONSTRAINT `chk_calificaciones_comida` CHECK (`comida` IS NULL OR `comida` BETWEEN 1 AND 5),
  CONSTRAINT `chk_calificaciones_atencion` CHECK (`atencion` IS NULL OR `atencion` BETWEEN 1 AND 5),
  CONSTRAINT `chk_calificaciones_completa` CHECK (`calificada_en` IS NULL OR (`comida` IS NOT NULL AND `atencion` IS NOT NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

SET @s := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tiendas' AND COLUMN_NAME = 'pedir_calificacion'), 'DO 0', 'ALTER TABLE `tiendas` ADD COLUMN `pedir_calificacion` tinyint(1) NOT NULL DEFAULT 1');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;
