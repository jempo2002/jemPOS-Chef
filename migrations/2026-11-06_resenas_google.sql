-- ============================================================
-- Migracion: resenas buenas a Google, malas solo al dueno
-- Fecha: 2026-11-06
-- Motor: MariaDB y MySQL 8/9
-- ============================================================
--
-- Va despues de 2026-10-10_calificaciones.sql. Se puede correr varias veces.
-- Solo crea estructura: agrega columnas vacias, no cambia datos existentes.
--
-- 1) sedes.google_resenas_url: enlace "Pedir resenas" de la ficha de Google
--    de cada sede (cada sede tiene su propia ficha en Google Maps). Vacio =
--    al cliente no se le invita a Google.
-- 2) calificaciones.google_en: primera vez que el cliente toco el boton de
--    Google despues de calificar (para contar cuantos se mandaron a Google).
-- 3) calificaciones.atendida_en / atendida_por / nota_atendida: el dueno
--    marca como atendida una calificacion baja (ya hablo con el cliente, ya
--    corrigio el problema). Deja de salir en "Por atender".

SET @s := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'sedes' AND COLUMN_NAME = 'google_resenas_url'), 'DO 0', 'ALTER TABLE `sedes` ADD COLUMN `google_resenas_url` varchar(300) DEFAULT NULL');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;

SET @s := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'calificaciones' AND COLUMN_NAME = 'google_en'), 'DO 0', 'ALTER TABLE `calificaciones` ADD COLUMN `google_en` datetime DEFAULT NULL');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;

SET @s := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'calificaciones' AND COLUMN_NAME = 'atendida_en'), 'DO 0', 'ALTER TABLE `calificaciones` ADD COLUMN `atendida_en` datetime DEFAULT NULL, ADD COLUMN `atendida_por` bigint(20) UNSIGNED DEFAULT NULL, ADD COLUMN `nota_atendida` varchar(255) DEFAULT NULL, ADD CONSTRAINT `fk_calificaciones_atendida_por` FOREIGN KEY (`atendida_por`) REFERENCES `usuarios` (`id_usuario`) ON UPDATE CASCADE');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;
