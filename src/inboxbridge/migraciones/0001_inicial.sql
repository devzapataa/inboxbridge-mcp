CREATE TABLE cuentas (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email          text NOT NULL UNIQUE,
    alias          text NOT NULL UNIQUE CHECK (alias ~ '^[a-z0-9][a-z0-9-]{0,29}$'),
    -- Cifrado con AES-256-GCM; el email va como dato asociado (ver crypto.py).
    refresh_token  bytea NOT NULL,
    scopes         text NOT NULL,
    estado         text NOT NULL DEFAULT 'activa' CHECK (estado IN ('activa', 'reconectar')),
    conectada_en   timestamptz NOT NULL DEFAULT now(),
    ultimo_uso     timestamptz
);

-- Solo metadatos: nunca el contenido de los correos ni las consultas.
CREATE TABLE auditoria (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    momento      timestamptz NOT NULL DEFAULT now(),
    herramienta  text NOT NULL,
    cuenta       text,
    resultado    text NOT NULL,
    duracion_ms  integer NOT NULL
);

CREATE INDEX auditoria_momento ON auditoria (momento DESC);
