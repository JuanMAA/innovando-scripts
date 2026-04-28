-- ============================================================
-- INNOVANDO — Schema completo de Supabase
-- Ejecutar en: innovando-test Y innovando-prd
-- Versión 1.0 · Abril 2025
-- ============================================================


-- ============================================================
-- TABLA 1: businesses
-- Lead central — todos los datos del negocio
-- ============================================================

create table businesses (
  id                          uuid primary key default gen_random_uuid(),

  -- Identificación
  place_id                    text unique not null,
  slug                        text unique not null,
  nombre                      text not null,
  categoria                   text,
  -- hotel | hostal | restaurante | tour_operador | cabaña
  ciudad                      text,
  pais                        text default 'Chile',
  telefono                    text,
  email                       text,
  direccion                   text,
  website_propio              text,
  calificacion                float,
  num_resenas                 int default 0,
  num_fotos                   int default 0,
  tiene_horarios              boolean default false,
  tiene_descripcion           boolean default false,
  latitud                     float,
  longitud                    float,

  -- Scores por módulo del reporte
  score_maps                  int default 0,   -- P2a 0-15 pts
  score_web                   int default 0,   -- P2b 0-20 pts
  score_reputacion            int default 0,   -- P2c 0-20 pts
  score_redes                 int default 0,   -- P2d 0-10 pts
  score_seo                   int default 0,   -- P2e 0-10 pts
  score_plataformas           int default 0,   -- P2f 0-25 pts
  score_total                 int default 0,   -- 0-100

  -- Score huella digital (informe separado)
  score_huella                int default 0,   -- 0-100

  -- Fechas de última actualización por módulo
  fecha_analisis_maps         timestamptz,
  fecha_analisis_web          timestamptz,
  fecha_analisis_redes        timestamptz,
  fecha_analisis_plataformas  timestamptz,
  fecha_analisis_seo          timestamptz,
  fecha_analisis_huella       timestamptz,

  -- Frecuencia de refresco por módulo (días)
  refresh_maps_dias           int default 30,
  refresh_web_dias            int default 30,
  refresh_redes_dias          int default 30,
  refresh_plataformas_dias    int default 30,
  refresh_huella_dias         int default 30,

  -- Notas del reporte (generadas por scripts Python)
  nota_general                text,
  nota_p2a                    text,
  nota_p2b                    text,
  nota_p2c                    text,
  nota_p2d                    text,
  nota_p2e                    text,
  nota_p2f                    text,
  nota_huella                 text,

  -- Flags de notas editadas manualmente por el admin
  -- Si true, los scripts no sobreescriben esa nota
  nota_general_editada        boolean default false,
  nota_p2a_editada            boolean default false,
  nota_p2b_editada            boolean default false,
  nota_p2c_editada            boolean default false,
  nota_p2d_editada            boolean default false,
  nota_p2f_editada            boolean default false,
  nota_huella_editada         boolean default false,

  -- Análisis web (scorer_web.py)
  web_ssl                     boolean,
  web_mobile                  boolean,
  web_velocidad               int,       -- ms de carga
  web_instagram               text,      -- URL perfil Instagram
  web_facebook                text,      -- URL perfil Facebook

  -- Plataformas de alojamiento (scorer_plataformas.py)
  booking_url                 text,
  booking_rating              float,
  booking_reviews             int,
  booking_fotos               int,
  booking_precio_avg          float,

  airbnb_url                  text,
  airbnb_rating               float,
  airbnb_reviews              int,
  airbnb_fotos                int,
  airbnb_precio_noche         float,
  airbnb_superhost            boolean,

  tripadvisor_url             text,
  tripadvisor_rating          float,
  tripadvisor_reviews         int,
  tripadvisor_fotos           int,
  tripadvisor_excelencia      boolean,

  expedia_url                 text,
  expedia_rating              float,
  expedia_reviews             int,

  despegar_url                text,
  despegar_rating             float,
  despegar_reviews            int,

  plataformas_count           int default 0,
  plataformas_rating_avg      float,

  -- Huella digital (scorer_huella.py)
  huella_rating_global        float,
  huella_total_resenas        int,
  huella_tendencia            text,
  -- mejorando | estable | bajando
  huella_palabras_positivas   jsonb,
  -- ["limpio", "amable", "ubicación"]
  huella_palabras_negativas   jsonb,
  -- ["wifi", "ruido", "precio"]
  huella_google_pagina1       boolean,
  huella_bing_pagina1_3       boolean,
  huella_chatgpt_mencionado   boolean,
  huella_total_fotos          int,
  huella_fotos_recientes      boolean,

  -- Diagnóstico personalizado de venta
  diagnostico_venta           text,

  -- Estado del lead en el pipeline
  estado                      text default 'nuevo',
  -- nuevo → analizado_web → analizado_redes → analizado
  -- → analizado_huella → contactado → seguimiento_1
  -- → seguimiento_2 → seguimiento_3 → reporte_pagado
  -- → huella_pagada → tutorial_pagado → sitio_activo
  -- → renovacion_pendiente → renovado | perdido

  -- Plan contratado
  plan                        text,
  -- null | reporte | huella | sitio_basico | sitio_premium

  -- Sitio web del cliente (si contrató)
  sitio_url                   text,
  sitio_activo                boolean default false,
  vercel_project_id           text,

  -- Control de pagos
  fecha_pago_reporte          timestamptz,
  fecha_pago_huella           timestamptz,
  fecha_pago_sitio            timestamptz,
  fecha_renovacion            timestamptz,

  -- Flag de problema reportado por cliente
  tiene_problema_reportado    boolean default false,

  -- Fechas generales
  fecha_scraping              date,
  fecha_alta                  timestamptz default now(),
  updated_at                  timestamptz default now()
);

-- Índices para búsquedas frecuentes
create index idx_businesses_slug on businesses(slug);
create index idx_businesses_place_id on businesses(place_id);
create index idx_businesses_estado on businesses(estado);
create index idx_businesses_ciudad on businesses(ciudad);
create index idx_businesses_pais on businesses(pais);
create index idx_businesses_score_total on businesses(score_total);


-- ============================================================
-- TABLA 2: source_links
-- Trazabilidad de fuentes — cada dato tiene su origen
-- ============================================================

create table source_links (
  id                  uuid primary key default gen_random_uuid(),
  business_id         uuid references businesses(id) on delete cascade,
  tipo                text not null,
  -- google_maps | sitio_web | subpagina_web | instagram | facebook |
  -- noticia | booking | airbnb | tripadvisor | expedia | despegar |
  -- google_search | bing_search
  url                 text not null,
  titulo              text,
  fuente              text,
  -- maps_scraper | scorer_web | scorer_redes | scorer_plataformas |
  -- scorer_huella | admin
  estado              text default 'activo',
  -- activo | roto | no_encontrado | desactualizado
  dato_asociado       text,
  -- ej: "calificacion_booking", "fotos_instagram", "precio_airbnb"
  ultima_verificacion timestamptz,
  fecha_extraccion    timestamptz default now(),
  metadata            jsonb
  -- {"rating": 4.2, "reviews": 38, "fotos": 12, "precio": 45}
);

create index idx_source_links_business_id on source_links(business_id);
create index idx_source_links_tipo on source_links(tipo);
create index idx_source_links_estado on source_links(estado);


-- ============================================================
-- TABLA 3: outreach_logs
-- Historial de emails y WhatsApp enviados
-- ============================================================

create table outreach_logs (
  id          uuid primary key default gen_random_uuid(),
  business_id uuid references businesses(id) on delete cascade,
  tipo        text not null,
  -- email_teaser | email_seguimiento_1 | email_seguimiento_2 |
  -- email_seguimiento_3 | whatsapp | email_reporte_completo |
  -- email_huella_digital | email_bienvenida_sitio | email_renovacion
  estado      text default 'enviado',
  -- enviado | abierto | respondio | rebotado
  enviado_at  timestamptz default now(),
  metadata    jsonb
  -- {"subject": "...", "sendgrid_id": "...", "email_to": "..."}
);

create index idx_outreach_logs_business_id on outreach_logs(business_id);
create index idx_outreach_logs_tipo on outreach_logs(tipo);
create index idx_outreach_logs_estado on outreach_logs(estado);
create index idx_outreach_logs_enviado_at on outreach_logs(enviado_at);


-- ============================================================
-- TABLA 4: payments
-- Registro de todos los pagos
-- ============================================================

create table payments (
  id                uuid primary key default gen_random_uuid(),
  business_id       uuid references businesses(id) on delete cascade,
  tipo              text not null,
  -- reporte | huella_digital | tutorial | sitio | renovacion
  monto             float not null,
  moneda            text default 'USD',
  -- USD | CLP | COP | ARS | BOB | PEN | MXN
  plataforma        text,
  -- mercadopago | paypal | webpay
  mp_payment_id     text,
  paypal_order_id   text,
  webpay_token      text,
  estado            text default 'pendiente',
  -- pendiente | aprobado | rechazado | reembolsado
  ambiente          text default 'prd',
  -- test | prd
  created_at        timestamptz default now()
);

create index idx_payments_business_id on payments(business_id);
create index idx_payments_tipo on payments(tipo);
create index idx_payments_estado on payments(estado);
create index idx_payments_created_at on payments(created_at);
create index idx_payments_ambiente on payments(ambiente);


-- ============================================================
-- TABLA 5: diy_tutorials
-- Contenido de tutoriales DIY — gestionado desde el dashboard
-- ============================================================

create table diy_tutorials (
  id              uuid primary key default gen_random_uuid(),
  slug            text unique not null,
  titulo          text not null,
  problema        text not null,
  -- campo que activa este tutorial en el reporte
  dificultad      text not null,
  -- facil | media | dificil
  precio          float default 0,
  -- 0 = gratis | 3 = $3 USD
  pasos           jsonb not null,
  -- [{"orden": 1, "texto": "Abrí Google Maps..."}, ...]
  servicio_cta    text,
  -- servicio profesional relacionado
  precio_servicio text,
  -- precio del servicio profesional
  activo          boolean default true,
  created_at      timestamptz default now(),
  updated_at      timestamptz default now()
);

create index idx_diy_tutorials_slug on diy_tutorials(slug);
create index idx_diy_tutorials_activo on diy_tutorials(activo);


-- ============================================================
-- TABLA 6: issue_reports
-- Problemas reportados por clientes desde el reporte web
-- ============================================================

create table issue_reports (
  id              uuid primary key default gen_random_uuid(),
  business_id     uuid references businesses(id) on delete cascade,
  tipo            text not null,
  -- perfil_equivocado | link_incorrecto | info_desactualizada
  detalle         text,
  -- descripción libre del cliente
  estado          text default 'pendiente',
  -- pendiente | revisado | resuelto | ignorado
  nota_interna    text,
  -- nota del admin, solo visible en el dashboard
  accion_tomada   text,
  -- re_analisis_completo | re_analisis_plataformas |
  -- re_analisis_web | edicion_manual | ignorado
  reportado_at    timestamptz default now(),
  resuelto_at     timestamptz
);

create index idx_issue_reports_business_id on issue_reports(business_id);
create index idx_issue_reports_estado on issue_reports(estado);
create index idx_issue_reports_reportado_at on issue_reports(reportado_at);


-- ============================================================
-- TABLA 7: country_pricing
-- Precios por país + categoría + servicio en moneda local
-- Gestionado desde el dashboard — sin tocar código
-- ============================================================

create table country_pricing (
  id              uuid primary key default gen_random_uuid(),
  pais            text not null,
  categoria       text not null,
  -- hotel | hostal | restaurante | tour_operador | cabaña | todos
  servicio        text not null,
  -- reporte_completo | huella_digital | tutorial | sitio_web |
  -- correccion_maps | setup_plataformas | setup_redes |
  -- gestion_reputacion | guia_diy
  precio          float not null,
  moneda          text not null,
  -- CLP | COP | ARS | BOB | PEN | MXN | USD
  precio_display  text not null,
  -- "$20.000 CLP" — para mostrar en UI
  activo          boolean default true,
  updated_at      timestamptz default now(),
  unique (pais, categoria, servicio)
);

create index idx_country_pricing_pais on country_pricing(pais);
create index idx_country_pricing_servicio on country_pricing(servicio);


-- ============================================================
-- DATOS INICIALES: country_pricing
-- Precios base para Chile — completar otros países desde dashboard
-- ============================================================

insert into country_pricing (pais, categoria, servicio, precio, moneda, precio_display) values
  ('Chile', 'todos', 'reporte_completo',   20000,  'CLP', '$20.000 CLP'),
  ('Chile', 'todos', 'huella_digital',     29000,  'CLP', '$29.000 CLP'),
  ('Chile', 'todos', 'tutorial',            3000,  'CLP', '$3.000 CLP'),
  ('Chile', 'todos', 'sitio_web',         149000,  'CLP', '$149.000 CLP'),
  ('Chile', 'todos', 'correccion_maps',    35000,  'CLP', '$35.000 CLP'),
  ('Chile', 'todos', 'setup_plataformas',  90000,  'CLP', '$90.000 CLP'),
  ('Chile', 'todos', 'setup_redes',        79000,  'CLP', '$79.000 CLP'),
  ('Chile', 'todos', 'gestion_reputacion', 79000,  'CLP', '$79.000 CLP/mes'),
  ('Chile', 'todos', 'guia_diy',           15000,  'CLP', '$15.000 CLP'),

  ('Colombia', 'todos', 'reporte_completo',  45000,  'COP', '$45.000 COP'),
  ('Colombia', 'todos', 'huella_digital',    69000,  'COP', '$69.000 COP'),
  ('Colombia', 'todos', 'tutorial',           5000,  'COP', '$5.000 COP'),
  ('Colombia', 'todos', 'sitio_web',        299000,  'COP', '$299.000 COP'),

  ('Argentina', 'todos', 'reporte_completo',  8500,  'ARS', '$8.500 ARS'),
  ('Argentina', 'todos', 'huella_digital',   12500,  'ARS', '$12.500 ARS'),
  ('Argentina', 'todos', 'tutorial',          1200,  'ARS', '$1.200 ARS'),
  ('Argentina', 'todos', 'sitio_web',        64000,  'ARS', '$64.000 ARS'),

  ('Bolivia', 'todos', 'reporte_completo',  65,  'BOB', 'Bs. 65'),
  ('Bolivia', 'todos', 'huella_digital',    95,  'BOB', 'Bs. 95'),
  ('Bolivia', 'todos', 'tutorial',           9,  'BOB', 'Bs. 9'),
  ('Bolivia', 'todos', 'sitio_web',        490,  'BOB', 'Bs. 490'),

  ('Perú', 'todos', 'reporte_completo',  55,  'PEN', 'S/. 55'),
  ('Perú', 'todos', 'huella_digital',    79,  'PEN', 'S/. 79'),
  ('Perú', 'todos', 'tutorial',          10,  'PEN', 'S/. 10'),
  ('Perú', 'todos', 'sitio_web',        399,  'PEN', 'S/. 399');


-- ============================================================
-- DATOS INICIALES: diy_tutorials
-- Tutoriales base — editar contenido desde el dashboard
-- ============================================================

insert into diy_tutorials (slug, titulo, problema, dificultad, precio, pasos, servicio_cta, precio_servicio) values
(
  'agregar-fotos-google-maps',
  'Agregá fotos a tu ficha de Google Maps',
  'num_fotos',
  'facil',
  0,
  '[
    {"orden": 1, "texto": "Abrí Google Maps en tu celular"},
    {"orden": 2, "texto": "Buscá el nombre de tu negocio"},
    {"orden": 3, "texto": "Tocá \"Agregar fotos\" en tu ficha"},
    {"orden": 4, "texto": "Subí mínimo 5 fotos de buena calidad"},
    {"orden": 5, "texto": "Esperá 24-48hs para que aparezcan publicadas"}
  ]'::jsonb,
  'Optimización profesional de ficha Google Maps',
  '$30–50 USD'
),
(
  'crear-perfil-booking',
  'Crear perfil en Booking.com',
  'booking_url',
  'media',
  3,
  '[
    {"orden": 1, "texto": "Ir a partner.booking.com y crear cuenta gratuita"},
    {"orden": 2, "texto": "Completar información básica del negocio"},
    {"orden": 3, "texto": "Subir mínimo 10 fotos de calidad"},
    {"orden": 4, "texto": "Configurar precios y disponibilidad"},
    {"orden": 5, "texto": "Esperar verificación de Booking (2-5 días hábiles)"}
  ]'::jsonb,
  'Setup profesional en plataformas de reserva',
  '$79–120 USD'
),
(
  'crear-perfil-airbnb',
  'Crear perfil en Airbnb',
  'airbnb_url',
  'media',
  3,
  '[
    {"orden": 1, "texto": "Ir a airbnb.com/host y crear cuenta de anfitrión"},
    {"orden": 2, "texto": "Completar el perfil del alojamiento paso a paso"},
    {"orden": 3, "texto": "Subir fotos profesionales (mínimo 10)"},
    {"orden": 4, "texto": "Configurar precio por noche y reglas de la casa"},
    {"orden": 5, "texto": "Activar el listado y recibir la primera reserva"}
  ]'::jsonb,
  'Setup profesional en plataformas de reserva',
  '$79–120 USD'
),
(
  'responder-resenas-negativas',
  'Cómo responder reseñas negativas correctamente',
  'score_reputacion',
  'media',
  3,
  '[
    {"orden": 1, "texto": "Respondé siempre — ignorar es peor que responder mal"},
    {"orden": 2, "texto": "Agradecé el feedback aunque sea negativo"},
    {"orden": 3, "texto": "No te defendas ni discutas en público"},
    {"orden": 4, "texto": "Ofrecé una solución concreta o invitá a contactarte"},
    {"orden": 5, "texto": "Usá tono profesional y breve — menos de 5 líneas"}
  ]'::jsonb,
  'Gestión profesional de reputación',
  '$59–150 USD/mes'
),
(
  'activar-google-business-profile',
  'Activar y verificar Google Business Profile',
  'score_maps',
  'media',
  3,
  '[
    {"orden": 1, "texto": "Ir a business.google.com e iniciar sesión con Gmail"},
    {"orden": 2, "texto": "Buscar tu negocio o crear uno nuevo"},
    {"orden": 3, "texto": "Elegir método de verificación (carta postal o llamada)"},
    {"orden": 4, "texto": "Completar toda la información: horarios, fotos, descripción"},
    {"orden": 5, "texto": "Publicar al menos 3 novedades en los primeros 30 días"}
  ]'::jsonb,
  'Corrección profesional de ficha Google Maps',
  '$30–50 USD'
);


-- ============================================================
-- VISTAS SQL
-- ============================================================

-- Ingresos por mes y tipo de servicio
create view v_revenue_by_month as
select
  date_trunc('month', created_at) as mes,
  tipo,
  sum(monto) as total,
  count(*) as cantidad,
  moneda
from payments
where estado = 'aprobado'
group by 1, 2, 5
order by 1 desc;

-- Ingresos por ciudad
create view v_revenue_by_city as
select
  b.ciudad,
  sum(p.monto) as total,
  count(*) as cantidad,
  round(avg(p.monto)::numeric, 2) as ticket_promedio
from payments p
join businesses b on p.business_id = b.id
where p.estado = 'aprobado'
group by b.ciudad
order by total desc;

-- Ingresos por país
create view v_revenue_by_country as
select
  b.pais,
  sum(p.monto) as total,
  count(*) as cantidad,
  round(avg(p.monto)::numeric, 2) as ticket_promedio,
  p.moneda
from payments p
join businesses b on p.business_id = b.id
where p.estado = 'aprobado'
group by b.pais, p.moneda
order by total desc;

-- Conversión por ciudad
create view v_conversion_by_city as
select
  ciudad,
  count(*) as total_leads,
  count(*) filter (where estado in ('contactado','reporte_pagado','sitio_activo')) as contactados,
  count(*) filter (where estado in ('reporte_pagado','sitio_activo')) as pagaron_reporte,
  count(*) filter (where estado = 'sitio_activo') as pagaron_sitio,
  round(
    count(*) filter (where estado in ('reporte_pagado','sitio_activo'))::numeric
    / nullif(count(*), 0)::numeric * 100, 1
  ) as tasa_conversion
from businesses
group by ciudad
order by tasa_conversion desc;

-- Conversión por país
create view v_conversion_by_country as
select
  pais,
  count(*) as total_leads,
  count(*) filter (where estado in ('reporte_pagado','huella_pagada','sitio_activo')) as pagaron,
  round(
    count(*) filter (where estado in ('reporte_pagado','huella_pagada','sitio_activo'))::numeric
    / nullif(count(*), 0)::numeric * 100, 1
  ) as tasa_conversion
from businesses
group by pais
order by tasa_conversion desc;

-- Outreach por tipo de mensaje
create view v_outreach_by_type as
select
  tipo,
  count(*) as enviados,
  count(*) filter (where estado = 'abierto') as abiertos,
  count(*) filter (where estado = 'respondio') as respondidos,
  round(
    count(*) filter (where estado = 'respondio')::numeric
    / nullif(count(*), 0)::numeric * 100, 1
  ) as tasa_respuesta
from outreach_logs
group by tipo
order by tasa_respuesta desc;

-- Reportes de problemas pendientes
create view v_pending_issues as
select
  ir.*,
  b.nombre,
  b.ciudad,
  b.pais,
  b.slug
from issue_reports ir
join businesses b on ir.business_id = b.id
where ir.estado = 'pendiente'
order by ir.reportado_at desc;

-- Países sin precios configurados
create view v_countries_without_pricing as
select distinct b.pais
from businesses b
where b.pais not in (
  select distinct pais
  from country_pricing
  where activo = true
)
order by b.pais;


-- ============================================================
-- FUNCIÓN: updated_at automático
-- Actualiza updated_at en businesses y country_pricing
-- ============================================================

create or replace function update_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

create trigger trg_businesses_updated_at
  before update on businesses
  for each row execute function update_updated_at();

create trigger trg_country_pricing_updated_at
  before update on country_pricing
  for each row execute function update_updated_at();

create trigger trg_diy_tutorials_updated_at
  before update on diy_tutorials
  for each row execute function update_updated_at();
