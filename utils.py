import json
from PyQt5 import QtCore, QtGui
from PyQt5.QtCore import Qt, pyqtSlot, QPointF, QRectF, QTimer, QFile, QTextStream, QUrl, QObject, pyqtSignal, QIODevice,QThread
from PyQt5.QtGui import QPixmap, QPainter, QColor, QPen, QPolygonF, QIcon, QBrush, QFont
import numpy as np
import sys
from PyQt5.QtWidgets import (QMainWindow, QApplication, QPushButton, QVBoxLayout, QStackedWidget, QSizePolicy,
                             QGraphicsScene, QGraphicsEllipseItem, QHBoxLayout, QLabel, QWidget, QFrame, QListWidget,
                             QScrollArea, QMessageBox, QGraphicsView, QGraphicsPixmapItem, QUndoStack, QUndoCommand,QFileDialog)

from PyQt5.QtWebChannel import QWebChannel
from PyQt5.QtWebEngineWidgets import QWebEngineView
from matplotlib.figure import Figure
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
import io
import folium
from design import Ui_window
import time
import paramiko
import json
import os         # Para manejar rutas de archivos locales
import posixpath  # Para manejar rutas de archivos remotas (en la Pi)


##Varaibles de conexion CAMBIARLAS CUANDO SE TRATE DE LA RASPBERRY
#Ip de tailscale del dispositivo
TAILSCALE_IP = "10.3.141.1"
#Nombre de usuario con el que se inicia sesion en el otro dispositivo
USERNAME = "pera"
#Contrasena
PASSWORD = "2314"

##Ruta CAMBIAR CUANDO SEA CON RASPBERRY
RUTA = "/home/pera/"

# Initialize SSH client
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())


class SftpWorker(QObject):
    """
    Worker que corre en un hilo separado para descargar archivos vía SFTP
    sin bloquear la GUI.
    """
    # Señales
    progress = pyqtSignal(str)       # (mensaje de progreso)
    finished = pyqtSignal()          # (descarga completada)
    error = pyqtSignal(str)          # (error)

    @pyqtSlot(str, str)
    def download_files(self, remote_dir, local_dir):
        """
        Descarga todos los archivos .jpg y .png de un directorio remoto.
        """
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            self.progress.emit("Conectando para transferencia de archivos...")
            client.connect(TAILSCALE_IP, username=USERNAME, password=PASSWORD, timeout=10)
            
            sftp = client.open_sftp()
            self.progress.emit(f"Accediendo a: {remote_dir}")
            
            files = sftp.listdir(remote_dir)
            
            # Filtra solo por imágenes
            images = [f for f in files if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
            
            if not images:
                self.error.emit("No se encontraron imágenes en el directorio.")
                sftp.close()
                client.close()
                return

            total_images = len(images)
            for i, fname in enumerate(images):
                self.progress.emit(f"Descargando {fname} ({i+1}/{total_images})...")
                
                remote_path = posixpath.join(remote_dir, fname)
                local_path = os.path.join(local_dir, fname)
                
                sftp.get(remote_path, local_path)

            sftp.close()
            client.close()
            self.progress.emit("¡Descarga completada!")
            self.finished.emit()

        except Exception as e:
            self.error.emit(f"Error de SFTP: {str(e)}")
        finally:
            if 'client' in locals() and client.get_transport() is not None:
                client.close()

class SshWorker(QObject):
    """
    Worker que corre en un hilo separado para manejar la conexión SSH
    y la ejecución de scripts sin bloquear la GUI.
    """
    # Señales para comunicarse con la página principal
    finished = pyqtSignal(str, str)  # (stdout, stderr)
    error = pyqtSignal(str)
    progress = pyqtSignal(str)       # Para enviar actualizaciones de estado

    @pyqtSlot(str)
    def run_ssh_command(self, json_payload):
        """
        Este es el método que se ejecuta en el hilo secundario.
        """
        try:
            # Re-inicializa el cliente DENTRO del hilo
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            self.progress.emit("Conectando al UAV...")
            client.connect(TAILSCALE_IP, username=USERNAME, password=PASSWORD, timeout=10)
            
            self.progress.emit("Ejecutando script de monitoreo...")
            cmd = f"bash -lc 'source /home/pera/venv_drone/bin/activate && python3 -u /home/pera/xdd3.py'"
            stdin, stdout, stderr = client.exec_command(cmd, get_pty=False)

            # Enviar JSON
            stdin.write(json_payload)
            stdin.flush()
            stdin.channel.shutdown_write()

            # Leer la salida línea por línea
            out_lines = []
            for line in iter(stdout.readline, ""):
                line = line.strip()
                if not line:
                    continue # Ignora líneas vacías
                
                # Emite CUALQUIER línea (sea de estado o %) como progreso
                self.progress.emit(line) 
                
                out_lines.append(line)
            
            out = "\n".join(out_lines)
            err = stderr.read().decode('utf-8')
            
            self.progress.emit("Script finalizado.")
            self.finished.emit(out, err)

        except Exception as e:
            self.error.emit(f"Error de conexión o ejecución: {str(e)}")
        finally:
            client.close()
            self.progress.emit("Conexión cerrada.")

class Bridge(QObject):
    # Signal to send the clicked coordinates to the main window
    mapClicked = pyqtSignal(float, float)

    @pyqtSlot(float, float)
    def onMapClicked(self, lat, lng):
        """
        This method is called from JavaScript when the map is clicked.
        It emits a signal to be handled by the Python application.
        """
        self.mapClicked.emit(lat, lng)


# --- PÁGINA TABLERO ---
class page_Tablero(QWidget):
    def __init__(self, parent=None):
        super(page_Tablero, self).__init__(parent)
        self.initUI()

    def initUI(self):
        self.setWindowTitle("Tablero de diagnosticos")
        layout = QVBoxLayout(self)
        # Título
        title_label = QLabel("Tablero estadístico")
        title_label.setStyleSheet("font-size: 20px; font-weight: bold;")
        layout.addWidget(title_label)
        # Contenedor principal de gráficos
        main_container = QHBoxLayout()
        # Gráfico de barras apiladas
        bar_chart_container = QVBoxLayout()
        bar_title = QLabel("Distribución de salud")
        bar_title.setStyleSheet("font-weight: bold;")
        bar_chart_container.addWidget(bar_title)
        # Simulación de datos
        dates = ["11/Ene/2024", "20/Feb/2025", "07/Mar/2025", "21/Abr/2025", "16/May/2025", "30/May/2025", "19/Jun/2025"]
        # Cada columna representa un día, con 5 niveles de salud
        data = [
            [30, 25, 20, 15, 10],  # 11/Ene/2024
            [28, 27, 22, 13, 10],
            [25, 28, 25, 12, 10],
            [22, 30, 28, 10, 10],
            [20, 32, 30, 8, 10],
            [18, 35, 32, 5, 10],
            [15, 40, 35, 5, 5]
        ]
        fig_bar = Figure(figsize=(8, 5), dpi=100)
        ax_bar = fig_bar.add_subplot(111)
        bottom = [0] * len(dates)
        colors = ['#00FF00', '#FFFF00', '#FFA500', '#FF4500', '#FF0000']  # Saludable -> Enfermo
        labels = ['Saludables', 'Leves rasgos', 'Rasgos considerables', 'Rasgos altos', 'Enfermas']
        for i in range(len(data[0])):
            values = [d[i] for d in data]
            ax_bar.bar(dates, values, bottom=bottom, color=colors[i], label=labels[i])
            bottom = [b + v for b, v in zip(bottom, values)]
        ax_bar.tick_params(axis='x', rotation=20, labelsize=8)
        ax_bar.set_ylabel('% del Total de diagnósticos')
        ax_bar.set_xlabel('Fecha de diagnóstico')
        ax_bar.legend(loc='upper right', fontsize=8)
        ax_bar.set_ylim(0, 100)
        canvas_bar = FigureCanvas(fig_bar)
        bar_chart_container.addWidget(canvas_bar)
        # Leyenda de colores
        legend_layout = QHBoxLayout()
        for i, (color, label) in enumerate(zip(colors, labels)):
            lbl = QLabel(label)
            lbl.setStyleSheet(f"background-color: {color}; padding: 5px; border-radius: 3px;")
            legend_layout.addWidget(lbl)
        bar_chart_container.addLayout(legend_layout)
        main_container.addLayout(bar_chart_container)
        # Gráfico de pastel y análisis
        pie_chart_container = QVBoxLayout()
        pie_title = QLabel("Último análisis")
        pie_title.setStyleSheet("font-weight: bold;")
        pie_chart_container.addWidget(pie_title)
        # Datos para el pie chart
        pie_data = [70, 15, 10, 5]  # Saludables, Leves, Considerables, Altos
        pie_labels = ['Saludables 70%', 'Con leves rasgos 15%', 'Con rasgos considerables 10%', 'Con rasgos altos 5%']
        fig_pie = Figure(figsize=(5, 5), dpi=100)
        ax_pie = fig_pie.add_subplot(111)
        wedges, texts, autotexts = ax_pie.pie(pie_data, labels=pie_labels, autopct='%1.1f%%', colors=colors[:4], startangle=90)
        ax_pie.axis('equal')
        canvas_pie = FigureCanvas(fig_pie)
        pie_chart_container.addWidget(canvas_pie)
        # Análisis textual
        analysis_text = """
        • 0% al 40% → Mejoró en un 10% el total de muestras saludables con respecto al último diagnóstico.
        • 41% al 65% → Se mantuvo el tamaño de muestras "con leves rasgos de enfermedad".
        • 66% al 100% → Disminuyó en un 5% el total de muestras "con rasgos considerables de enfermedad".
        """
        analysis_label = QLabel(analysis_text)
        analysis_label.setWordWrap(True)
        pie_chart_container.addWidget(analysis_label)
        main_container.addLayout(pie_chart_container)
        layout.addLayout(main_container)
        layout.addStretch()


# --- PÁGINA DIAGNOSTICAR ---
class page_diagnosticar(QWidget):
    # --- AÑADE ESTA SEÑAL ---
    # Señal para iniciar el trabajo de SSH desde el hilo principal
    start_ssh = pyqtSignal(str)
    start_sftp_download = pyqtSignal(str, str)

    def __init__(self):
        super().__init__()

        self.PREDEFINED_SAVE_PATH = os.path.join(os.path.expanduser("~"), "Drone_Mission_Photos")
        self.PREDEFINED_REMOTE_PATH = "/home/pera/Downloads/photos/photo_path"

        self.current_step = 0
        self.conectado = False

        self.perimeter_points = []
        self.start_point = None

        # --- AÑADE ESTO: Configuración del Hilo y Worker ---
        self.ssh_thread = QThread(self)
        self.ssh_worker = SshWorker()
        
        # Mover el worker al hilo
        self.ssh_worker.moveToThread(self.ssh_thread)

        # Conectar señales y slots
        # 1. Iniciar el trabajo cuando emitamos 'start_ssh'
        self.start_ssh.connect(self.ssh_worker.run_ssh_command)

        # 2. Recibir los resultados cuando el worker termine
        self.ssh_worker.finished.connect(self.on_ssh_finished)
        self.ssh_worker.error.connect(self.on_ssh_error)
        self.ssh_worker.progress.connect(self.on_ssh_progress)

        # 3. Limpieza: Asegurarse de que el hilo se cierre
        self.ssh_thread.finished.connect(self.ssh_worker.deleteLater)
        # (Asegúrate de llamar a self.ssh_thread.quit() cuando cierres la app)

        # Iniciar el hilo (estará esperando señales)
        self.ssh_thread.start()
        # --- FIN DE LA SECCIÓN AÑADIDA ---

        # --- AÑADE ESTO: Configuración del Hilo y Worker SFTP ---
        self.sftp_thread = QThread(self)
        self.sftp_worker = SftpWorker()
        self.sftp_worker.moveToThread(self.sftp_thread)

        # Conectar señales y slots
        self.start_sftp_download.connect(self.sftp_worker.download_files)
        self.sftp_worker.progress.connect(self.on_download_progress)
        self.sftp_worker.finished.connect(self.on_download_complete)
        self.sftp_worker.error.connect(self.on_ssh_error) # Podemos reusar el slot de error

        # Iniciar el hilo SFTP
        self.sftp_thread.start()
        # --- FIN DE LA SECCIÓN AÑADIDA ---

        self.initUI()

    def initUI(self):
        self.layout = QVBoxLayout(self)
        self.stacked_widget = QStackedWidget()
        self.layout.addWidget(self.stacked_widget)

        # Se crean las páginas en orden
        self.page0 = self.create_page0()
        self.stacked_widget.addWidget(self.page0)

        self.page1 = self.create_page1()
        self.stacked_widget.addWidget(self.page1)

        self.page2 = self.create_page2()
        self.stacked_widget.addWidget(self.page2)

        self.page3 = self.create_page3()
        self.stacked_widget.addWidget(self.page3)

        self.page4 = self.create_page4()
        self.stacked_widget.addWidget(self.page4)

        self.update_page()

    # --- PÁGINA 1: MODIFICADA ---
    def create_page0(self):
        page = QWidget()
        # Guarda el layout para poder añadirle cosas después
        self.page1_layout = QVBoxLayout(page)

        title = QLabel("Paso previo del Punto de Despegue")
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        self.page1_layout.addWidget(title)

        # 1. Crea el mensaje de "bloqueado"
        self.locked_label = QLabel("Conecta el UAV para comenzar.")
        self.locked_label.setStyleSheet(
            "color: Black; font-size: 20px; font-weight: bold; qproperty-alignment: 'AlignCenter';")
        self.page1_layout.addWidget(self.locked_label)

        return page


    # --- PÁGINA 1: SELECCIÓN DE PERÍMETRO (4 PUNTOS) ---
    def create_page1(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        title = QLabel("Paso 1: Punto de Despegue")
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(title)

        # Nombres de variables únicos para esta página
        self.status_label1 = QLabel("Ubicación actual del UAV.")
        self.status_label1.setStyleSheet("color: green; font-size: 19px;")
        layout.addWidget(self.status_label1)

        self.coord_list_widget1 = QListWidget()
        self.coord_list_widget1.setMaximumHeight(20)
        layout.addWidget(self.coord_list_widget1)

        self.web_view1 = QWebEngineView()
        layout.addWidget(self.web_view1, 1)

        # Puente de comunicación único para este mapa
        self.bridge1 = Bridge()
        self.channel1 = QWebChannel()
        self.channel1.registerObject("bridge", self.bridge1)
        self.web_view1.page().setWebChannel(self.channel1)
        #self.bridge1.mapClicked.connect(self.handle_start_point_map_click)
        self.web_view1.setHtml(self.get_map_html(), QUrl("qrc:///"))

        # Botones
        #btn_deshacer = QPushButton("Limpiar Selección")
        #btn_deshacer.setStyleSheet("background-color: #f44336; color: white;")
        #btn_deshacer.clicked.connect(self.clear_start_point_marker)
        btn_actualizar = QPushButton("Actualizar puntos")
        btn_actualizar.setStyleSheet("background-color: #1D8777; color: white;")
        btn_actualizar.clicked.connect(self.up_to_date_map1)

        btn_siguiente = QPushButton("Siguiente")
        btn_siguiente.setStyleSheet("background-color: #4CAF50; color: white;")
        btn_siguiente.clicked.connect(self.go_to_step2)

        btn_layout = QHBoxLayout()
        btn_layout.addWidget(btn_actualizar)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_siguiente)
        layout.addLayout(btn_layout)

        return page

    # --- PÁGINA 2: SELECCIÓN DE PUNTO DE INICIO (1 PUNTO) ---
    def create_page2(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        title = QLabel("Paso 2: Área de monitoreo")
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(title)

        # Usamos nombres de variables únicos para esta página
        self.status_label2 = QLabel("Haz clic en el mapa para seleccionar los 4 puntos del perímetro.")
        self.status_label2.setStyleSheet("color: green;")
        layout.addWidget(self.status_label2)

        self.coord_list_widget2 = QListWidget()
        self.coord_list_widget2.setMaximumHeight(80)
        layout.addWidget(self.coord_list_widget2)

        self.web_view2 = QWebEngineView()
        layout.addWidget(self.web_view2, 1)

        # Puente de comunicación único para este mapa
        self.bridge2 = Bridge()
        self.channel2 = QWebChannel()
        self.channel2.registerObject("bridge", self.bridge2)
        self.web_view2.page().setWebChannel(self.channel2)
        self.bridge2.mapClicked.connect(self.handle_perimeter_map_click)
        self.web_view2.setHtml(self.get_map_html(), QUrl("qrc:///"))

        # Botones
        btn_regresar = QPushButton("Regresar")
        btn_regresar.setStyleSheet("background-color: #B2ADA9; color: black;")
        btn_regresar.clicked.connect(self.come_back_to_step1)

        btn_deshacer = QPushButton("Limpiar Selección")
        btn_deshacer.setStyleSheet("background-color: #f44336; color: white;")
        btn_deshacer.clicked.connect(self.clear_perimeter_markers)

        btn_actualizar = QPushButton("Actualizar puntos")
        btn_actualizar.setStyleSheet("background-color: #1D8777; color: white;")
        btn_actualizar.clicked.connect(self.up_to_date_map2)

        btn_siguiente = QPushButton("Siguiente")
        btn_siguiente.setStyleSheet("background-color: #4CAF50; color: white;")
        btn_siguiente.clicked.connect(self.go_to_step3)

        btn_layout = QHBoxLayout()
        btn_layout.addWidget(btn_regresar)
        btn_layout.addWidget(btn_deshacer)
        btn_layout.addWidget(btn_actualizar)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_siguiente)
        layout.addLayout(btn_layout)

        return page

    def create_page3(self):
        page = QWidget()
        layout = QVBoxLayout(page)

        title = QLabel("Realizando diagnostico!")
        title.setStyleSheet("font-size: 26px; font-weight: bold;")
        layout.addWidget(title)

        # --- MODIFICADO ---
        # Dales nombres con 'self.'
        self.progress_status_label = QLabel("Iniciando conexión...")
        self.progress_status_label.setStyleSheet("color: Black; font-size: 20px; font-weight: bold; qproperty-alignment: 'AlignCenter';")
        
        self.progress_percent_label = QLabel("0% monitoreado")
        self.progress_percent_label.setStyleSheet("font-size: 16px;")
        
        layout.addWidget(self.progress_percent_label)
        layout.addWidget(self.progress_status_label)
        # --- FIN MODIFICADO ---

        btn_abortar = QPushButton("Abortar operación")
        btn_abortar.setStyleSheet("background-color: #f44336; color: white;")
        btn_abortar.clicked.connect(self.abort) # (Nota: Abortar un hilo requiere más lógica)

        # --- MODIFICADO ---
        # Dale un nombre al botón y deshabilítalo al inicio
        self.btn_page3_siguiente = QPushButton("Siguiente")
        self.btn_page3_siguiente.setStyleSheet("background-color: #4CAF50; color: white;")
        self.btn_page3_siguiente.clicked.connect(self.go_to_step4)
        self.btn_page3_siguiente.setEnabled(False) # <--- Deshabilitado
        # --- FIN MODIFICADO ---

        btn_layout = QHBoxLayout()
        btn_layout.addWidget(btn_abortar)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_page3_siguiente) # Usamos la variable de 'self'
        layout.addLayout(btn_layout)

        return page

    def create_page4(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(15, 15, 15, 15)  # Añade un poco de espacio
        layout.setSpacing(10)

        # --- Títulos ---
        title = QLabel("¡Diagnóstico Finalizado!")
        title.setStyleSheet("font-size: 24px; font-weight: bold; color: green;")
        layout.addWidget(title)

        subtitle = QLabel("Resultados de diagnóstico - 02/Ago/2025 - 10:02 AM")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("font-size: 14px; color: #555;")  # Estilo sutil
        layout.addWidget(subtitle)

        # --- Tarjeta de Contenido Principal (Blanca) ---
        content_card = QFrame()
        content_card.setObjectName("contentCard")
        content_card.setFrameShape(QFrame.StyledPanel)

        content_layout = QHBoxLayout(content_card)  # Layout principal dentro de la tarjeta

        # --- 1. Panel Izquierdo: Mapa de Folium ---
        map_frame = QFrame()
        map_layout = QVBoxLayout(map_frame)
        map_layout.setContentsMargins(0, 0, 0, 0)

        self.web_view4 = QWebEngineView()
        layout.addWidget(self.web_view4, 1)

        # Puente de comunicación único para este mapa
        self.bridge4 = Bridge()
        self.channel4 = QWebChannel()
        self.channel4.registerObject("bridge", self.bridge4)
        self.web_view4.page().setWebChannel(self.channel4)
        self.web_view4.setHtml(self.get_map_html(), QUrl("qrc:///"))

        map_layout.addWidget(self.web_view4)
        content_layout.addWidget(map_frame, 2)

        # --- 2. Panel Derecho: Gráfico y Botones ---
        right_panel_layout = QVBoxLayout()
        right_panel_layout.setSpacing(15)

        # --- Gráfico Circular ---
        sizes = [72, 13, 18, 11, 15]
        labels_data = [
            'Saludables', 'Con leves rasgos', 'Con rasgos considerables',
            'Altos rasgos de enfermedad', 'Enferma'
        ]
        # Creamos etiquetas personalizadas como en la imagen
        labels = [f'{l}\n{s}' for l, s in zip(labels_data, sizes)]
        colors = ['#4CAF50', '#ADFF2F', '#FFEB3B', '#FF9800', '#F44336']  # Ajuste de colores

        fig = Figure(figsize=(5, 4), dpi=100)
        fig.patch.set_alpha(0.0)  # Fondo transparente

        ax = fig.add_subplot(111)
        ax.set_title('Clasificación del total de fotos tomadas', fontsize=12)

        ax.pie(sizes, labels=labels, colors=colors, startangle=90,
               textprops={'fontsize': 9})
        ax.axis('equal')  # Asegura que el gráfico sea un círculo

        canvas = FigureCanvas(fig)
        canvas.setStyleSheet("background-color: transparent;")
        right_panel_layout.addWidget(canvas)

        # --- Botones de Acción ---
        btn_layout = QVBoxLayout()
        btn_guardar = QPushButton(" Guardar")
        btn_guardar.setIcon(QIcon.fromTheme("document-save"))  # Añadir icono

        btn_imprimir = QPushButton(" Imprimir")
        btn_imprimir.setIcon(QIcon.fromTheme("document-print"))  # Añadir icono

        btn_ver_fotos = QPushButton(" Ver fotos")
        btn_ver_fotos.setIcon(QIcon.fromTheme("camera-photo"))  # Añadir icono

        btn_terminar = QPushButton("Terminar")
        btn_terminar.setStyleSheet("background-color: #4CAF50; color: white;")
        btn_terminar.setObjectName("btnTerminar")  # ID especial para estilo

        # Conexiones
        #btn_guardar.clicked.connect(lambda: QMessageBox.information(self, "Guardar", "Guardado exitoso"))
        btn_guardar.clicked.connect(lambda: QMessageBox.information(self, "Guardar", "Guardando..."))
        btn_imprimir.clicked.connect(lambda: QMessageBox.information(self, "Imprimir", "Imprimiendo..."))
        btn_ver_fotos.clicked.connect(lambda: QMessageBox.information(self, "Ver fotos", "Mostrando fotos..."))
        btn_terminar.clicked.connect(self.reset_diagnostic_ended)

        btn_layout.addWidget(btn_guardar)
        btn_layout.addWidget(btn_imprimir)
        btn_layout.addWidget(btn_ver_fotos)
        btn_layout.addSpacing(10)
        btn_layout.addWidget(btn_terminar)

        right_panel_layout.addLayout(btn_layout)
        right_panel_layout.addStretch()  # Empuja los botones hacia arriba

        content_layout.addLayout(right_panel_layout, 1)  # Dar al panel derecho menos espacio (factor 1)

        layout.addWidget(content_card)  # Añadir la tarjeta blanca al layout principal

        # --- Barra de Estado (Inferior) ---
        status_bar_frame = QFrame()
        status_bar_frame.setObjectName("statusBar")
        status_bar_frame.setFrameShape(QFrame.StyledPanel)

        status_bar_layout = QHBoxLayout(status_bar_frame)
        status_bar_layout.setContentsMargins(15, 10, 15, 10)
        status_bar_layout.setSpacing(15)

        # (Aquí puedes añadir iconos si los tienes)
        status_bar_layout.addWidget(QLabel("Sensores: Buen estado"))
        status_bar_layout.addWidget(QLabel("Batería: 41%"))
        status_bar_layout.addStretch()  # Espaciador
        status_bar_layout.addWidget(QLabel("Tiempo de análisis: 0h 39 min"))
        status_bar_layout.addWidget(QLabel("Tiempo de vuelo: 7 min"))

        layout.addWidget(status_bar_frame)

        # --- Barra de Estado (Inferior) ---
        status_bar_frame = QFrame()
        status_bar_frame.setObjectName("statusBar")  # ID para CSS
        status_bar_frame.setFrameShape(QFrame.StyledPanel)
        status_bar_frame.setMinimumHeight(65)  # Altura mínima

        # Layout principal (horizontal)
        status_bar_layout = QHBoxLayout(status_bar_frame)
        status_bar_layout.setContentsMargins(20, 5, 20, 5)  # Más margen horizontal
        status_bar_layout.setSpacing(25)

        # --- Item 1: Sensores ---
        item1_layout = QHBoxLayout()
        icon1_label = QLabel()
        # NOTA: ¡Reemplaza 'ruta/a/tu/icono_drone.png' por tu icono real!
        # icon1_label.setPixmap(QPixmap("ruta/a/tu/icono_drone.png").scaled(40, 40, Qt.KeepAspectRatio))
        icon1_label.setPixmap(QIcon.fromTheme('network-wired').pixmap(40, 40))  # Placeholder
        icon1_label.setMinimumSize(40, 40)
        item1_layout.addWidget(icon1_label)

        text1_layout = QVBoxLayout()
        text1_layout.setSpacing(0)
        text1_layout.addStretch()
        text1_layout.addWidget(QLabel("Sensores"))
        val1_label = QLabel("Buen estado")
        val1_label.setStyleSheet("font-weight: bold;")
        text1_layout.addWidget(val1_label)
        text1_layout.addStretch()
        item1_layout.addLayout(text1_layout)
        status_bar_layout.addLayout(item1_layout)

        # --- Item 2: Batería ---
        item2_layout = QHBoxLayout()
        icon2_label = QLabel()
        # NOTA: ¡Reemplaza 'ruta/a/tu/icono_bateria.png' por tu icono real!
        # icon2_label.setPixmap(QPixmap("ruta/a/tu/icono_bateria.png").scaled(40, 40, Qt.KeepAspectRatio))
        icon2_label.setPixmap(QIcon.fromTheme('battery').pixmap(40, 40))  # Placeholder
        icon2_label.setMinimumSize(40, 40)
        item2_layout.addWidget(icon2_label)

        text2_layout = QVBoxLayout()
        text2_layout.setSpacing(0)
        text2_layout.addStretch()
        text2_layout.addWidget(QLabel("Batería"))
        val2_label = QLabel("65%")  # Valor de la imagen
        val2_label.setStyleSheet("font-weight: bold;")
        text2_layout.addWidget(val2_label)
        text2_layout.addStretch()
        item2_layout.addLayout(text2_layout)
        status_bar_layout.addLayout(item2_layout)

        # Espaciador central
        status_bar_layout.addStretch()

        # --- Item 3: Tiempo de análisis ---
        item3_layout = QHBoxLayout()
        icon3_label = QLabel()
        # NOTA: ¡Reemplaza 'ruta/a/tu/icono_reloj.png' por tu icono real!
        # icon3_label.setPixmap(QPixmap("ruta/a/tu/icono_reloj.png").scaled(40, 40, Qt.KeepAspectRatio))
        icon3_label.setPixmap(QIcon.fromTheme('appointment-new').pixmap(40, 40))  # Placeholder
        icon3_label.setMinimumSize(40, 40)
        item3_layout.addWidget(icon3_label)

        text3_layout = QVBoxLayout()
        text3_layout.setSpacing(0)
        text3_layout.addStretch()
        text3_layout.addWidget(QLabel("Tiempo de análisis"))
        val3_label = QLabel("1 h 15 min")  # Valor de la imagen
        val3_label.setStyleSheet("font-weight: bold;")
        text3_layout.addWidget(val3_label)
        text3_layout.addStretch()
        item3_layout.addLayout(text3_layout)
        status_bar_layout.addLayout(item3_layout)

        # --- Item 4: Tiempo de vuelo ---
        item4_layout = QHBoxLayout()
        icon4_label = QLabel()
        # NOTA: ¡Reemplaza 'ruta/a/tu/icono_vuelo.png' por tu icono real!
        # icon4_label.setPixmap(QPixmap("ruta/a/tu/icono_vuelo.png").scaled(40, 40, Qt.KeepAspectRatio))
        icon4_label.setPixmap(QIcon.fromTheme('camera-photo').pixmap(40, 40))  # Placeholder [cite: 35]
        icon4_label.setMinimumSize(40, 40)
        item4_layout.addWidget(icon4_label)

        text4_layout = QVBoxLayout()
        text4_layout.setSpacing(0)
        text4_layout.addStretch()
        text4_layout.addWidget(QLabel("Tiempo de vuelo"))
        val4_label = QLabel("27 min")  # Valor de la imagen
        val4_label.setStyleSheet("font-weight: bold;")
        text4_layout.addWidget(val4_label)
        text4_layout.addStretch()
        item4_layout.addLayout(text4_layout)
        status_bar_layout.addLayout(item4_layout)

        # Añadir la barra de estado completa al layout principal de la página
        layout.addWidget(status_bar_frame)


        return page

    def get_map_html(self):
        """
        Devuelve el código HTML/JS para el mapa Leaflet.
        --- MODIFICADO ---
        - addMarker() ahora acepta un color y dibuja Círculos.
        - Añadida la función drawPolygon().
        - clearMarkers() ahora limpia también los polígonos.
        """
        return """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Map</title>
            <meta charset="utf-8" />
            <meta name="viewport" content="width=device-width, initial-scale-1.0">

            <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
            <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>

            <script src="qrc:///qtwebchannel/qwebchannel.js"></script>

            <style>
                body { margin: 0; padding: 0; }
                #map { height: 100vh; width: 100%; }
            </style>
        </head>
        <body>
            <div id="map"></div>
            <script>
                // 1. Inicializar el mapa
                var map = L.map('map').setView([20.432939, -99.598862], 30);

                // Capa de satélite
                L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
                    attribution: 'mapa interactuable',
                    maxNativeZoom: 18,
                    maxZoom: 22
                }).addTo(map);

                // --- NUEVAS CAPAS ---
                var markerLayer = L.layerGroup().addTo(map); // Para los puntos
                var polygonLayer = L.layerGroup().addTo(map); // Para el polígono
                var pythonBridge; 

                // 2. Configurar WebChannel
                new QWebChannel(qt.webChannelTransport, function(channel) {
                    pythonBridge = channel.objects.bridge;
                });

                // 3. Manejar clic del mapa
                map.on('click', function(e) {
                    if (pythonBridge) {
                        pythonBridge.onMapClicked(e.latlng.lat, e.latlng.lng);
                    }
                });

                // --- FUNCIONES MODIFICADAS ---

                /**
                 * Dibuja un círculo de color en el mapa.
                 * @param {float} lat - Latitud
                 * @param {float} lng - Longitud
                 * @param {string} color - Un color CSS (ej. 'red', '#ff0000', 'lightgreen')
                 */
                function addMarker(lat, lng, color) {
                    L.circleMarker([lat, lng], {
                        radius: 1,
                        color: color,         // Color del borde
                        fillColor: color,     // Color de relleno
                        fillOpacity: 0.8
                    }).addTo(markerLayer);
                }

                /**
                 * Dibuja un polígono basado en una lista de puntos.
                 * @param {string} points_json - Un string JSON de un array de coordenadas.
                 */
                function drawPolygon(points_json) {
                    polygonLayer.clearLayers(); // Limpia polígonos anteriores
                    var points = JSON.parse(points_json);

                    if (points && points.length >= 3) {
                        L.polygon(points, {
                            color: 'red',       // Color del borde
                            weight: 2,
                            fillColor: '#ff0000', // Color de relleno (rojo)
                            fillOpacity: 0.2    // Relleno rojo transparente
                        }).addTo(polygonLayer);
                    }
                }

                /**
                 * Limpia ambas capas, marcadores y polígonos.
                 */
                function clearMarkers() {
                    markerLayer.clearLayers();
                    polygonLayer.clearLayers();
                }

                function updateCenter(lat, lng) {
                    map.setView([lat, lng]);
                }
            </script>
        </body>
        </html>
        """

    @pyqtSlot(bool, float, float)
    def set_estado_conexion(self, conectado, lat, long):
        """
        Este es el SLOT que recibe la señal desde MainWindow.
        """
        self.coordenadas_iniciales = (lat, long)  
        self.conectado = conectado
        if conectado:
            self.go_to_step1()

    # --- MANEJADORES DE CLIC (SEPARADOS) ---
    @pyqtSlot(float, float)
    def handle_start_point_map_click(self, lat, lng):
        if self.start_point is not None:
            item_text = f"Punto de inicio: ({lat:.5f}, {lng:.5f})"
            self.coord_list_widget1.addItem(item_text)
            self.web_view1.page().runJavaScript(f"addMarker({lat}, {lng}, 'lightgreen');")
            self.status_label1 = QLabel("Ubicación actual del UAV.")
            self.status_label1.setStyleSheet("color: green; font-size: 19px;")
        else:
            self.status_label1.setText("Solo se puede seleccionar 1 punto. Limpie para reiniciar.")

    @pyqtSlot(float, float)
    def handle_perimeter_map_click(self, lat, lng):
        if len(self.perimeter_points) < 4:
            self.perimeter_points.append((lat, lng))
            item_text = f"Punto {len(self.perimeter_points)}: ({lat:.5f}, {lng:.5f})"
            self.coord_list_widget2.addItem(item_text)

            self.web_view2.page().runJavaScript(f"addMarker({lat}, {lng}, 'red');")

            # Si ya tenemos 3 o 4 puntos, dibujamos el polígono.
            if len(self.perimeter_points) >= 3:
                # Convertimos la lista de tuplas de Python a un string JSON
                # que JavaScript pueda entender (ej. [[lat,lng], [lat,lng], ...])
                points_json = json.dumps(self.perimeter_points)

                # Pasamos el string JSON a nuestra nueva función JS
                self.web_view2.page().runJavaScript(f"drawPolygon('{points_json}');")

            if len(self.perimeter_points) == 4:
                self.status_label2.setText("Perímetro de 4 puntos seleccionado.")
        else:
            self.status_label2.setText("Máximo de 4 puntos alcanzado. Limpie para reiniciar.")


    # --- MÉTODOS DE LIMPIEZA (SEPARADOS) ---
    def clear_start_point_marker(self):
        self.start_point = self.coordenadas_iniciales  ######### pendiente
        self.coord_list_widget1.clear()
        self.web_view1.page().runJavaScript("clearMarkers();")
        self.status_label1 = QLabel("Ubicación actual del UAV.")
        self.status_label1.setStyleSheet("color: green; font-size: 19px;")

    def up_to_date_map1(self):
        # Borrando puntos del mapa y actualizando ubicación actual
        self.web_view1.page().runJavaScript("clearMarkers();")
        self.web_view1.page().runJavaScript(f"addMarker({self.start_point[0]}, {self.start_point[1]}, 'lightgreen');")

    def up_to_date_map2(self):
        # Borrando puntos del mapa y actualizando ubicación actual
        # dibujando el punto de despegue y aterrizaje
        self.web_view2.page().runJavaScript("clearMarkers();")
        self.web_view2.page().runJavaScript(f"addMarker({self.start_point[0]}, {self.start_point[1]}, 'lightgreen');")
        # dibujando el área
        for lat, lng in self.perimeter_points:
            self.web_view2.page().runJavaScript(f"addMarker({lat}, {lng}, 'red');")
        else:
            points_json = json.dumps(self.perimeter_points)
            self.web_view2.page().runJavaScript(f"drawPolygon('{points_json}');")

    def clear_perimeter_markers(self):
        self.perimeter_points = []
        self.coord_list_widget2.clear()
        self.web_view2.page().runJavaScript("clearMarkers();")
        self.web_view2.page().runJavaScript(f"addMarker({self.start_point[0]}, {self.start_point[1]}, 'lightgreen');")
        self.status_label2.setText("Haz clic en el mapa para seleccionar los 4 puntos del perímetro.")


    def go_to_step1(self):
        if self.conectado:
            self.current_step = 1
            #self.coordenadas_iniciales = (20.432939, -99.598862)  ######### pendiente
            lat, lng = self.coordenadas_iniciales
            self.start_point = (lat, lng)
            zoom = 18
            self.web_view1.page().runJavaScript(f"map.setView([{lat}, {lng}], {zoom});")
            self.web_view1.page().runJavaScript(f"addMarker({lat}, {lng}, 'lightgreen');")
            self.handle_start_point_map_click(lat, lng)
            self.update_page()

    def go_to_step2(self):
        if self.start_point is not None:
            self.current_step = 2
            lat, lng = self.start_point
            zoom = 18
            self.web_view2.page().runJavaScript(f"map.setView([{lat}, {lng}], {zoom});")
            if self.start_point is not None:
                self.web_view2.page().runJavaScript(f"addMarker({lat}, {lng}, 'lightgreen');")
            self.update_page()
        else:
            QMessageBox.warning(self, "Error", "Debes seleccionar un punto de despegue.")

    def go_to_step3(self):
        # (Tu coordenada manual no se usa, la he comentado)
        # coordenadas_manuales = [(18.888597, -99.023020),(18.888596, -99.022982),(18.888565, -99.023013),(18.888569, -99.022972)]
        
        if len(self.perimeter_points) == 4:
            # 1. Cambia de página INMEDIATAMENTE. Esto ahora funciona.
            self.current_step = 3
            self.update_page()
            
            # 2. Resetea el estado de la página 3
            self.btn_page3_siguiente.setEnabled(False)
            self.progress_status_label.setText("Preparando script...")
            self.progress_percent_label.setText("0%")

            # 3. Prepara los datos
            json_payload = json.dumps(self.perimeter_points)
            
            # 4. Emite la señal para que el worker (en el otro hilo) comience.
            #    La GUI NO se bloqueará.
            self.start_ssh.emit(json_payload)
            
            # (Se eliminó todo el código bloqueante de paramiko)
        else:
            QMessageBox.warning(self, "Error", "Debes seleccionar 4 puntos.")


    def go_to_step4(self):
        """
        Este método inicia la descarga desde la RUTA REMOTA predefinida
        hacia la RUTA LOCAL predefinida.
        """
        # 1. Obtiene ambas rutas predefinidas
        local_save_dir = self.PREDEFINED_SAVE_PATH
        remote_save_dir = self.PREDEFINED_REMOTE_PATH # <-- Usa la nueva variable
        
        # (El 'if not self.remote_photos_dir:' ya no es necesario)

        # 2. Asegúrate de que el directorio local exista
        try:
            # os.makedirs con exist_ok=True es como "mkdir -p"
            os.makedirs(local_save_dir, exist_ok=True)
        except Exception as e:
            QMessageBox.critical(self, "Error de Directorio", f"No se pudo crear la carpeta local: {local_save_dir}\nError: {e}")
            return
            
        # 3. Inicia la descarga
        self.progress_status_label.setText(f"Descargando de {remote_save_dir}...")
        self.progress_percent_label.setText("") # Limpiar porcentaje
        self.btn_page3_siguiente.setEnabled(False) # Deshabilitar botón durante la descarga
        
        # 4. Emite la señal con AMBAS rutas predefinidas
        self.start_sftp_download.emit(remote_save_dir, local_save_dir)

    def come_back_to_step1(self):
        self.clear_perimeter_markers()
        #self.clear_start_point_marker()
        self.current_step = 1
        self.update_page()

    def abort(self):
        self.current_step = 4
        self.update_page()

    def reset_diagnostic(self):
        self.current_step = 1 if self.conectado else 0
        self.clear_perimeter_markers()
        self.clear_start_point_marker()
        self.update_page()

    def reset_diagnostic_ended(self):
        self.clear_perimeter_markers()
        self.clear_start_point_marker()
        self.go_to_step1()

    def update_page(self):
        self.stacked_widget.setCurrentIndex(self.current_step)

    def _show_page_4(self):
        """
        Esta función contiene la LÓGICA ORIGINAL de tu go_to_step4.
        Se encarga de preparar y mostrar la página 4.
        """
        if len(self.perimeter_points) == 4:
            self.current_step = 4
            # Limpiar, centrar y marcar mapa
            self.web_view4.page().runJavaScript("clearMarkers();")
            self.web_view4.page().runJavaScript(f"map.setView([{self.start_point[0]}, {self.start_point[1]}], {18});")
            self.web_view4.page().runJavaScript(
                f"addMarker({self.start_point[0]}, {self.start_point[1]}, 'lightgreen');")
            # Marca del área de monitoreo
            for p in self.perimeter_points:
                self.web_view4.page().runJavaScript(f"addMarker({p[0]}, {p[1]}, 'red');")
            else:
                points_json = json.dumps(self.perimeter_points)
                self.web_view4.page().runJavaScript(f"drawPolygon('{points_json}');")
            
            self.update_page() # <-- Esto cambia a la página 4
        else:
             QMessageBox.warning(self, "Error", "Debes seleccionar exactamente 4 puntos para el perímetro.")

    # --- AÑADE ESTOS 3 MÉTODOS NUEVOS ---

    @pyqtSlot(str)
    def on_ssh_progress(self, message):
        """
        Este slot se activa cada vez que el worker emite 'progress'.
        Ahora distingue entre mensajes de estado y de porcentaje.
        """
        if "%" in message:
            # Es un mensaje de porcentaje
            self.progress_percent_label.setText(message)
        elif "success" not in message and "error" not in message: 
            # Es un mensaje de estado (e ignoramos el JSON final)
            # (Asumiendo que tus mensajes de estado no contienen la palabra "success")
            self.progress_status_label.setText(message)

    @pyqtSlot(str, str)
    def on_ssh_finished(self, out, err):
        """
        Este slot se activa CUANDO el script termina exitosamente.
        """
        self.progress_status_label.setText("¡Diagnóstico completado!")
        self.progress_percent_label.setText("100% monitoreado")
        
        # Habilita el botón "Siguiente"
        self.btn_page3_siguiente.setEnabled(True)
        
        # (Opcional) Puedes imprimir la salida para depurar
        print("--- SALIDA DEL SCRIPT ---")
        print(out)
        if err:
            print("--- ERRORES DEL SCRIPT ---")
            print(err)

    @pyqtSlot(str)
    def on_ssh_error(self, error_message):
        """
        Este slot se activa si el worker falla (ej. error de conexión).
        """
        QMessageBox.critical(self, "Error de Diagnóstico", error_message)
        # Enviamos al usuario de vuelta al paso 1
        self.come_back_to_step1()
    
    @pyqtSlot(str)
    def on_download_progress(self, message):
        """
        Se activa con el worker SFTP para actualizar el estado.
        """
        self.progress_status_label.setText(message)

    @pyqtSlot()
    def on_download_complete(self):
        """
        Se activa CUANDO el worker SFTP termina.
        Ahora, finalmente, vamos a la página 4.
        """
        self.progress_status_label.setText("¡Descarga completa!")
        # Re-habilita el botón por si acaso
        self.btn_page3_siguiente.setEnabled(True) 
        
        # Ahora sí, vamos a la página de resultados
        self._show_page_4()
           


# --- PÁGINA ESTADÍSTICOS (propuesta) ---
class page_Estadisticas(QWidget):
    def __init__(self, parent=None):
        super(page_Estadisticas, self).__init__(parent)
        self.initUI()


    def initUI(self):
        self.setWindowTitle("Tablero de estadisticas")
        layout = QVBoxLayout(self)
        # Título
        title_label = QLabel("Estadísticas Generales")
        title_label.setStyleSheet("font-size: 20px; font-weight: bold;")
        layout.addWidget(title_label)
        # Contenedor principal
        main_container = QHBoxLayout()
        # Gráfico de líneas (evolución mensual)
        line_chart_container = QVBoxLayout()
        line_title = QLabel("Evolución mensual de diagnósticos")
        line_title.setStyleSheet("font-weight: bold;")
        line_chart_container.addWidget(line_title)
        fig_line = Figure(figsize=(8, 5), dpi=100)
        ax_line = fig_line.add_subplot(111)
        months = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun']
        diagnoses = [120, 150, 180, 200, 220, 250]  # Ejemplo de datos
        ax_line.plot(months, diagnoses, marker='o', linewidth=2, color='#007BFF')
        ax_line.set_ylabel('Cantidad de diagnósticos')
        ax_line.set_xlabel('Mes')
        ax_line.grid(True, linestyle='--', alpha=0.6)
        canvas_line = FigureCanvas(fig_line)
        line_chart_container.addWidget(canvas_line)
        main_container.addLayout(line_chart_container)
        # Resumen numérico
        summary_container = QVBoxLayout()
        summary_title = QLabel("Resumen General")
        summary_title.setStyleSheet("font-weight: bold;")
        summary_container.addWidget(summary_title)
        stats = [
            ("Total diagnósticos", "1,220"),
            ("Promedio mensual", "203"),
            ("Diagnósticos saludables", "85%"),
            ("Diagnósticos con problemas", "15%"),
            ("Mejora promedio", "+8%")
        ]
        for label, value in stats:
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            val_label = QLabel(value)
            val_label.setStyleSheet("font-weight: bold; font-size: 16px;")
            row.addWidget(val_label)
            row.addStretch()
            summary_container.addLayout(row)
        main_container.addLayout(summary_container)
        layout.addLayout(main_container)
        # Botón de exportar
        export_btn = QPushButton("Exportar reporte")
        export_btn.setStyleSheet("background-color: #007BFF; color: white; padding: 10px;")
        layout.addWidget(export_btn)
        layout.addStretch()

class InteractiveMapView(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene = QGraphicsScene()
        self.setScene(self.scene)
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        self.setInteractive(True)

        # Cargar imagen base (reemplaza con tu propia imagen)
        self.background_pixmap = QPixmap("./icons/field_map.png")  # Asegúrate de tener esta imagen
        if self.background_pixmap.isNull():
            self.background_pixmap = QPixmap(600, 400)
            self.background_pixmap.fill(Qt.lightGray)

        self.bg_item = QGraphicsPixmapItem(self.background_pixmap)
        self.scene.addItem(self.bg_item)

        self.selected_point = None
        self.perimeter_points = []
        self.polygon_item = None
        self.parent = parent

    def wheelEvent(self, event):
        factor = 1.2 if event.angleDelta().y() > 0 else 0.8
        self.scale(factor, factor)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            pos = self.mapToScene(event.pos())
            if isinstance(self.parent, page_diagnosticar):
                if self.parent.current_step == 0:  # Punto de despegue
                    self.clear_selection()
                    self.selected_point = pos
                    self.draw_point(pos)
                    if hasattr(self.parent, 'status_label'):
                        self.parent.status_label.setText(f"Ubicación seleccionada: ({pos.x():.6f}, {pos.y():.6f})")
                elif self.parent.current_step == 1:  # Perímetro
                    self.perimeter_points.append(pos)
                    self.draw_point(pos, color=Qt.red)
                    if len(self.perimeter_points) >= 3:
                        self.draw_polygon()

    def draw_point(self, pos, color=Qt.green):
        ellipse = self.scene.addEllipse(pos.x()-5, pos.y()-5, 10, 10, QPen(color), QBrush(color))
        ellipse.setZValue(10)

    def draw_polygon(self):
        if self.polygon_item:
            self.scene.removeItem(self.polygon_item)
        if len(self.perimeter_points) >= 3:
            polygon = QPolygonF([QPointF(p.x(), p.y()) for p in self.perimeter_points])
            self.polygon_item = self.scene.addPolygon(polygon, QPen(Qt.red, 2), QBrush(Qt.transparent))
            self.polygon_item.setZValue(5)

    def clear_selection(self):
        self.selected_point = None
        self.perimeter_points = []
        if self.polygon_item:
            self.scene.removeItem(self.polygon_item)
            self.polygon_item = None
        # Limpiar todos los puntos
        for item in self.scene.items():
            if isinstance(item, QGraphicsEllipseItem):
                self.scene.removeItem(item)

    def reset(self):
        self.clear_selection()
        self.scene.clear()
        self.bg_item = QGraphicsPixmapItem(self.background_pixmap)
        self.scene.addItem(self.bg_item)