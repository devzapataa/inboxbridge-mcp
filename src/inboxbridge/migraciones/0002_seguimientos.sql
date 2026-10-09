-- Memoria de las rutinas: cada plazo o proceso que hay que vigilar, con el #número que
-- aparece en los avisos y que el dueño usa en los comandos (hecho 12, posponer 12 lunes).
CREATE TABLE seguimientos (
    id              integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cuenta          text NOT NULL,
    hilo_id         text NOT NULL,
    titulo          text NOT NULL CHECK (char_length(titulo) BETWEEN 1 AND 200),
    tipo            text NOT NULL DEFAULT 'otro'
                    CHECK (tipo IN ('empleo', 'legal', 'pago', 'tramite', 'otro')),
    estado          text NOT NULL DEFAULT 'pendiente'
                    CHECK (estado IN ('pendiente', 'avisado', 'hecho', 'descartado', 'vencido')),
    vence_en        timestamptz,
    proximo_paso    text CHECK (char_length(proximo_paso) <= 300),
    recordar_desde  date,
    evento_id       text,
    borrador_id     text,
    nota            text CHECK (char_length(nota) <= 500),
    creado_en       timestamptz NOT NULL DEFAULT now(),
    actualizado_en  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (cuenta, hilo_id)
);

CREATE INDEX seguimientos_activos ON seguimientos (estado, vence_en);
