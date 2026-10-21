# Yerno Millonario – Descargador a PDF (Flask)

Descarga los capítulos desde el 6947, los agrupa en PDFs de 10 capítulos
(`Capítulos_6947-6956.pdf`, ...) y los guarda en `/sdcard/yerno millonario`.

## Estructura
```
yerno_millonario/
├── app.py               # servidor Flask + hilo de descarga + generación PDF
├── requirements.txt
└── templates/index.html # interfaz oscura (móvil y PC)
```
En la carpeta de salida se crean:
- `estado.json` → siguiente capítulo, inicio del bloque y lista de PDFs.
- `.cache/` → capítulos ya descargados que todavía no forman un PDF (para no perder nada al pausar o cerrar).

## Ejecutar en Termux
```bash
pkg update && pkg upgrade -y
pkg install -y python fontconfig-utils ttf-dejavu   # DejaVu = acentos y comillas en el PDF
termux-setup-storage                                 # acepta el permiso de almacenamiento
pip install -r requirements.txt
python app.py
```
- En el mismo teléfono: http://127.0.0.1:5000
- Desde otro dispositivo de la misma Wi-Fi: http://IP_DEL_TELEFONO:5000
  (la IP se ve con `ifconfig` o `ip addr show wlan0`, por ejemplo 192.168.1.50)

Para que Android no detenga el proceso con la pantalla apagada: `termux-wake-lock`
(se quita con `termux-wake-unlock`).

## Uso
- **Iniciar / Reanudar**: arranca el hilo en segundo plano; continúa desde `estado.json`.
- **Pausar**: termina el capítulo en curso y se detiene; el estado queda guardado.
- **Generar PDF parcial**: (solo en pausa) crea un PDF con los capítulos del bloque actual aunque no sean 10.
- **Unir PDFs**: junta todos los PDFs completados en `Yerno_Millonario_<primero>-<último>_completo.pdf`,
  con un marcador por bloque. Se ejecuta en segundo plano (puede hacerse mientras se descarga) y aparece en "PDFs unidos".
- **Capítulo inicial**: (solo en pausa) escribe un número y pulsa **Aplicar** para continuar desde ese capítulo,
  o **Reiniciar** para volver al 6947. Los PDFs ya creados se conservan; los capítulos del bloque sin terminar se descartan.
- Si un capítulo aún no se ha publicado (404), espera 10 minutos y vuelve a intentarlo.

## Variables opcionales
| Variable    | Por defecto              | Uso                          |
|-------------|--------------------------|------------------------------|
| `YM_OUTPUT` | `/sdcard/yerno millonario` | carpeta de salida          |
| `YM_START`  | `6947`                   | capítulo inicial (solo sin estado previo) |
| `PORT`      | `5000`                   | puerto del servidor          |

Ejemplo: `YM_OUTPUT="$HOME/ym" python app.py`

## API
| Método | Ruta              | Acción                          |
|--------|-------------------|---------------------------------|
| GET    | `/`               | interfaz web                    |
| GET    | `/api/status`     | estado, progreso, log y PDFs    |
| POST   | `/api/start`      | iniciar                         |
| POST   | `/api/resume`     | reanudar                        |
| POST   | `/api/pause`      | pausar                          |
| POST   | `/api/flush`      | PDF parcial del bloque actual   |
| POST   | `/api/merge`      | unir PDFs (opcional `{"from": 6947, "to": 7046}`) |
| POST   | `/api/set_start`  | `{"chapter": 7000}` o `{"reset": true}` |
| GET    | `/download/<pdf>` | descarga directa                |

Para borrar también la lista de PDFs: elimina `estado.json` y la carpeta `.cache`.
