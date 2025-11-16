

TAILSCALE_IP = "10.3.141.1"
#Nombre de usuario con el que se inicia sesion en el otro dispositivo
USERNAME = "pera"
#Contrasena
PASSWORD = "2314"

##Ruta CAMBIAR CUANDO SEA CON RASPBERRY
RUTA = "/home/pera/"

comando = "bash -lc 'source "+ RUTA + "venv_drone/bin/activate && python3 -u " + RUTA + "basic_flight_test_takeoff.py'"
print(comando)
import paramiko


##Conectar por SSH y ejecutar un comando que devuelva latitud y longitud.
def obtener_gps_ssh(host, username, password, command):
    
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(hostname=host, username=username, password=password)

        stdin, stdout, stderr = ssh.exec_command(command)
        print("okay")
        salida = stdout.read().decode().strip()
        ssh.close()

        lat_str, lon_str = salida.split()
        print("xddd")
        return float(lat_str), float(lon_str)
    
    except Exception as e:
        print("Error SSH:", e)
        return None, None
##Aqui se hace la conexion
obtener_gps_ssh(TAILSCALE_IP, USERNAME, PASSWORD, comando)