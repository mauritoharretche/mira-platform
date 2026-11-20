# MIRA — Libro operativo

## Intent

El operador identifica un producto, distingue el dato registrado de una edición pendiente y decide la siguiente acción con confianza. La interfaz debe sentirse serena, precisa y responsable. En catálogo, el producto y su estado administrativo tienen prioridad sobre la gestión de categorías. En login, la acción principal es ingresar con una cuenta habilitada.

Esta dirección describe presentación e interacción existentes. No introduce búsqueda, métricas, vendibilidad, precios agregados ni operaciones que el backend no exponga.

## Firma

La ficha comercial distingue **registrado** de **en edición o pendiente de revisión**. El estado del servidor nunca se presenta como confirmación de una escritura incierta. Cada operación conserva su propia confirmación, conflicto, error o incertidumbre, junto a su formulario. El catálogo expresa únicamente activación administrativa; no adelanta estados de precio, stock o venta.

## Fundamentos

| Token | Valor | Uso |
| --- | --- | --- |
| `--canvas` | `#F5F6F2` | Fondo de trabajo, como papel claro |
| `--surface` | `#FFFFFF` | Listas, formularios y paneles |
| `--surface-inset` | `#F0F3EF` | Controles y zonas de lectura secundaria |
| `--ink` | `#1D2A23` | Texto principal |
| `--ink-secondary` | `#35473B` | Etiquetas y valores de apoyo |
| `--muted` | `#516157` | Información secundaria |
| `--muted-soft` | `#64736A` | Metadatos legibles |
| `--accent` | `#24583E` | Marca, enlaces y acción principal |
| `--accent-hover` | `#193F2B` | Acción principal al apuntar |
| `--border` | `#D6DED7` | Separación estructural discreta |
| `--control-border` | `#78877E` | Borde perceptible de controles |
| `--focus` | `#8A5411` | Foco visible, distinto del verde |

Advertencia, error y éxito conservan texto y borde además de color. Los pares candidatos de texto principal, secundario, verde sobre blanco, foco sobre blanco y borde de control sobre blanco superan respectivamente 14:1, 6:1, 8:1, 6:1 y 3:1 por cálculo de color; deben medirse de nuevo en los estados renderizados antes de publicar.

### Tipografía

Pila de sistema local, sin descarga de fuentes. Cuerpo de lectura 15–16 px y line-height 1.5; entradas de texto y selectores de 16 px, acciones y navegación de 13–14 px con área interactiva de al menos 44 px. Escala de títulos cercana a 30 / 20 / 16 px, con peso y color para distinguir niveles sin agrandar todos los encabezados. Números de stock, precio, secuencias y revisiones usan cifras tabulares. Los nombres largos pueden ocupar varias líneas sin truncar información operativa.

### Espaciado, superficies y profundidad

Base de 4 px: 4 / 8 / 12 / 16 / 24 / 32. Filas de catálogo compactas con área interactiva mínima de 44 px; controles de al menos 44 × 44 px. Paneles con 16–24 px de relleno en escritorio y 16 px en móvil. Bordes redondeados de 8 px en controles y 12 px en superficies.

La profundidad se expresa con cambios tonales leves; no se usan sombras decorativas. Los bordes separan filas y delimitan entradas, sin crear una cuadrícula pesada alrededor de cada elemento.

## Patrones

- **Navegación:** barra superior compacta con Catálogo y Nuevo producto; `aria-current`, salto al contenido y cierre de sesión siguen visibles. No hay módulos futuros en la navegación.
- **Listado:** una lista semántica de productos, alineada como registro en escritorio y reordenada como tarjetas compactas en móvil. Nombre primero; categoría y activación administrativa después. El conteo, si aparece, corresponde sólo a la página recibida.
- **Categorías:** complemento del catálogo. En móvil se presentan después de los productos; crear categoría sigue sujeto a su capability y mantiene su advertencia de resultado incierto.
- **Formularios:** etiquetas visibles, ayuda próxima al control, errores específicos, foco y razones explícitas de indisponibilidad. Se priorizan controles HTML nativos.
- **Feedback:** carga con texto y `role=status`; errores con `role=alert`; confirmaciones, conflictos y resultados inciertos no se ocultan mediante un toast temporal. No hay reintento automático de POST.
- **Iconos:** si se incorporan, son SVG locales de apoyo, de 16–20 px, con texto en acciones críticas. No se añade biblioteca de iconos.
- **Movimiento:** cambios breves de color o superficie en hover/active; sin desplazamientos animados de filas ni transiciones en escrituras frecuentes. `prefers-reduced-motion` elimina movimiento no esencial.

## Responsive y accesibilidad

En 1280 × 800 deben verse al menos diez filas de nombres cortos. En 390 × 844 el primer producto aparece antes de la creación de categorías. No hay desbordamiento horizontal de página a 320 px. Validar además 768, 1024 y 1440 px, texto al 200 % y zoom real al 200 % y 400 %. Navegación con Tab y Shift+Tab, foco al cambiar de pantalla, targets de 44 px, nombres accesibles y contraste se comprueban en el navegador. Las capturas de login se hacen antes de llenar credenciales.

## Alcance de UX-04A

Fundamentos, shell, login y catálogo. La ficha existente recibe sólo los tokens globales compartidos; su estructura y los estados de variante se rediseñarán en un incremento posterior. Mantener `ProductWorkspace`, `OperationPermitCoordinator`, contratos HTTP, permisos, CSRF, cursores y rutas sin cambios funcionales.
