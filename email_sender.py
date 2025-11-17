import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import os


def send_report_email(recipient_email, pdf_path, fecha):
    """
    Envía un email con el reporte PDF adjunto.

    Args:
        recipient_email (str): Email del destinatario.
        pdf_path (str): Ruta al archivo PDF que se adjuntará.
    """

    # --- Configuración del Emisor (¡EDITAR ESTO!) ---
    # IMPORTANTE: Para Gmail, debes usar una "App Password"
    # generada desde la configuración de seguridad de tu cuenta de Google.
    SENDER_EMAIL = "villedapatricioaronalejandro@gmail.com"
    SENDER_PASSWORD = "aift xdmi snkh unyk"
    # --- Fin Configuración ---

    # --- Configuración del Servidor SMTP (Gmail) ---
    SMTP_SERVER = "smtp.gmail.com"
    SMTP_PORT = 587  # Puerto para TLS

    # --- Crear el Mensaje ---
    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = recipient_email
    msg['Subject'] = "Reporte de Diagnóstico de Cultivo"

    # --- Cuerpo del Email ---
    body = f"""
    Hola,

    El monitoreo del cultivo celebrado el {fecha} ha finalizado.
    Se adjunta el reporte en PDF con los resultados detallados.

    Saludos
    """
    msg.attach(MIMEText(body, 'plain'))

    # --- Adjuntar el Archivo PDF ---
    filename = os.path.basename(pdf_path)

    try:
        with open(pdf_path, "rb") as attachment:
            # Crear la parte MIME para el adjunto
            part = MIMEBase('application', 'octet-stream')
            part.set_payload(attachment.read())

        # Codificar en base64
        encoders.encode_base64(part)

        # Añadir cabecera
        part.add_header(
            'Content-Disposition',
            f'attachment; filename= {filename}',
        )

        # Adjuntar al mensaje
        msg.attach(part)
    except Exception as e:
        raise IOError(f"No se pudo leer el archivo adjunto {pdf_path}: {e}")

    # --- Enviar el Email ---
    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()  # Iniciar conexión segura
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        text = msg.as_string()
        server.sendmail(SENDER_EMAIL, recipient_email, text)
        server.quit()
        print(f"Email enviado exitosamente a {recipient_email}")

    except smtplib.SMTPAuthenticationError:
        raise Exception("Error de autenticación. Revisa tu email y contraseña de aplicación.")
    except Exception as e:
        raise Exception(f"Error de SMTP: {e}")

# (Puedes probar este script directamente si descomentas las siguientes líneas)
# if __name__ == "__main__":
#     try:
#         send_report_email("email_destinatario@ejemplo.com", "./diagnosticos_guardados/Reporte_20251117_011900.pdf")
#     except Exception as e:
#         print(e)