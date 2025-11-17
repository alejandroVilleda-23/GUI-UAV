import json
import sys
import io
import datetime
import os
import posixpath  # Necesario para rutas SFTP
import paramiko  # Necesario para SSH/SFTP

from PyQt5 import QtCore, QtGui
from PyQt5.QtCore import (Qt, pyqtSlot, QPointF, QRectF, QTimer, QFile, QTextStream, QUrl,
                          QObject, pyqtSignal, QIODevice, QThread)
from PyQt5.QtGui import (QPixmap, QPainter, QColor, QPen, QPolygonF, QIcon, QBrush, QFont)
from PyQt5.QtWidgets import (QMainWindow, QApplication, QPushButton, QVBoxLayout, QStackedWidget, QSizePolicy,
                             QGraphicsScene, QGraphicsEllipseItem, QHBoxLayout, QLabel, QWidget, QFrame, QListWidget,
                             QScrollArea, QMessageBox, QGraphicsView, QGraphicsPixmapItem, QUndoStack, QUndoCommand,
                             QButtonGroup, QRadioButton, QListWidgetItem, QInputDialog)

from PyQt5.QtWebChannel import QWebChannel
from PyQt5.QtWebEngineWidgets import QWebEngineView
from matplotlib.figure import Figure
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas

import folium
from geopy.distance import geodesic
from shapely.geometry import Polygon
from pyproj import Geod

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph
from reportlab.lib.colors import HexColor

# Imports propios (asumo que existen según tu instrucción)
from design import Ui_window
from email_sender import *
from predictor import *

# --- CONFIGURACIÓN DE CONEXIÓN (Del antiguo utils.py) ---
TAILSCALE_IP = "10.3.141.1"
USERNAME = "pera"
PASSWORD = "2314"


# ========================================================
# CLASES WORKER (HILOS EN SEGUNDO PLANO)
# ========================================================

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


class SshWorker(QObject):
    """
    Worker que corre en un hilo separado para manejar la conexión SSH
    y la ejecución de scripts sin bloquear la GUI.
    """
    finished = pyqtSignal(str, str)  # (stdout, stderr)
    error = pyqtSignal(str)
    progress = pyqtSignal(str)  # Para enviar actualizaciones de estado

    @pyqtSlot(str)
    def run_ssh_command(self, json_payload):
        client = None
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            self.progress.emit("Conectando al UAV...")
            client.connect(TAILSCALE_IP, username=USERNAME, password=PASSWORD, timeout=10)

            self.progress.emit("Ejecutando script de monitoreo...")
            # Ajusta la ruta a tu script real
            cmd = f"bash -lc 'source /home/pera/venv_drone/bin/activate && python3 -u /home/pera/xdd3.py'"
            stdin, stdout, stderr = client.exec_command(cmd, get_pty=False)

            stdin.write(json_payload)
            stdin.flush()
            stdin.channel.shutdown_write()

            out_lines = []
            for line in iter(stdout.readline, ""):
                line = line.strip()
                if not line: continue
                self.progress.emit(line)  # Enviar cada línea de progreso
                out_lines.append(line)

            out = "\n".join(out_lines)
            err = stderr.read().decode('utf-8')

            self.progress.emit("Script finalizado.")
            self.finished.emit(out, err)

        except Exception as e:
            self.error.emit(f"Error de conexión o ejecución: {str(e)}")
        finally:
            if client:
                client.close()


class SftpWorker(QObject):
    progress = pyqtSignal(str)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    @pyqtSlot(str, str)
    def download_files(self, remote_dir, local_dir):
        client = None
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            self.progress.emit("Conectando para transferencia de archivos...")
            client.connect(TAILSCALE_IP, username=USERNAME, password=PASSWORD, timeout=10)

            sftp = client.open_sftp()
            self.progress.emit(f"Accediendo a: {remote_dir}")

            try:
                files = sftp.listdir(remote_dir)
            except FileNotFoundError:
                self.error.emit(f"Directorio remoto no encontrado: {remote_dir}")
                return

            images = [f for f in files if f.lower().endswith(('.jpg', '.jpeg', '.png'))]

            if not images:
                self.error.emit("No se encontraron imágenes en el directorio remoto.")
                sftp.close()
                client.close()
                return

            total_images = len(images)
            for i, fname in enumerate(images):
                self.progress.emit(f"Descargando {fname} ({i + 1}/{total_images})...")
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
            if client:
                client.close()


class PredictionWorker(QObject):
    """
    Worker que corre la predicción de IA en un hilo separado.
    """
    # Señal de finalizado emite los diccionarios resultantes
    finished = pyqtSignal(dict, dict, dict, dict, list)
    progress = pyqtSignal(str)
    error = pyqtSignal(str)

    @pyqtSlot(str)
    def run_prediction(self, photos_path):
        try:
            self.progress.emit("Iniciando procesamiento de IA...")

            # Etiquetas para las clasificaciones
            CLASES_DEL_MODELO = [
                'common_rust', 'gray_leaf_spot', 'healthy',
                'northern_leaf_blight', 'northern_leaf_spot'
            ]
            LABEL_PROBABILIDAD = [
                'Saludables', 'Leves rasgos', 'Rasgos considerables',
                'Rasgos altos', 'Enfermas'
            ]

            # Hiperparámetros
            NUM_CLASES = 5
            PATH_MODELO = "./models/densenet_201_fold4.pth"

            # Carga y ejecución del modelo
            classifier = ImageClassifier(
                model_path=PATH_MODELO,
                num_classes=NUM_CLASES,
                class_names=CLASES_DEL_MODELO
            )

            # Predicción
            current_results = classifier.predict_folder(photos_path)

        except Exception as e:
            self.error.emit(f"Error al cargar modelo o predecir carpeta: {str(e)}")
            return

        if not current_results:
            self.error.emit("El modelo no devolvió resultados.")
            return

        # Conteo de resultados
        class_counts = {class_name: 0 for class_name in CLASES_DEL_MODELO}
        class_file_lists = {class_name: [] for class_name in CLASES_DEL_MODELO}
        state_file_lists = {class_name: [] for class_name in LABEL_PROBABILIDAD}
        class_leaf_state = {class_name: 0 for class_name in LABEL_PROBABILIDAD}

        errores = 0

        for filename, info in current_results.items():
            try:
                # Clase predicha
                predicted_class = info['clase']
                if predicted_class in class_counts:
                    class_counts[predicted_class] += 1
                    class_file_lists[predicted_class].append(filename)

                    # Intento de obtener coordenadas del nombre
                    try:
                        coord_raw = os.path.splitext(filename)[0].split(", ")
                        coord = [float(c) for c in coord_raw]
                    except:
                        coord = [0.0, 0.0]

                    # Agregamos coordenada a la lista de clases
                    # Nota: La lógica original usaba listas de coords en class_file_lists?
                    # En utils_2 parecia guardar coords, ajustamos:
                    if isinstance(class_file_lists[predicted_class], list):
                        # Si la lista estaba guardando filenames, cuidado.
                        # En utils_2.py guardaba coords. Haremos lo mismo.
                        class_file_lists[predicted_class].pop()  # Quitamos filename
                        class_file_lists[predicted_class].append(coord)

                # Confianza y estado
                confianza = float(info['confianza healthy'])
                if 85.0 <= confianza <= 100.0:
                    class_leaf_state["Saludables"] += 1
                    state_file_lists["Saludables"].append(coord)
                elif 65.0 <= confianza < 85.0:
                    class_leaf_state["Leves rasgos"] += 1
                    state_file_lists["Leves rasgos"].append(coord)
                elif 30.0 <= confianza < 65.0:
                    class_leaf_state["Rasgos considerables"] += 1
                    state_file_lists["Rasgos considerables"].append(coord)
                elif 15.0 <= confianza < 30.0:
                    class_leaf_state["Rasgos altos"] += 1
                    state_file_lists["Rasgos altos"].append(coord)
                elif 0.0 <= confianza < 15.0:
                    class_leaf_state["Enfermas"] += 1
                    state_file_lists["Enfermas"].append(coord)

            except Exception as e:
                errores += 1
                self.progress.emit(f"Error procesando {filename}: {str(e)}")

        nuevos_conteos = list(class_leaf_state.values())
        self.finished.emit(class_counts, class_file_lists, class_leaf_state, state_file_lists, nuevos_conteos)


# ========================================================
# INTERFAZ GRÁFICA (BASE: utils_2.py)
# ========================================================

# --- PÁGINA TABLERO ---
class page_Tablero(QWidget):
    def __init__(self, parent=None):
        super(page_Tablero, self).__init__(parent)
        self.class_counts = {}
        self.list_results_per_class = {}
        self.list_leaf_state = {}

        # Inicializa con datos placeholder para que se muestre algo
        self.dates = ['Esperando...']
        self.data = [[100.0]]  # 100% de una categoría
        self.data_is_placeholder = True  # Un flag para saber que son datos de inicio

        self.label_colores_prob_enfermedad = ['#006A35', '#34A853', '#FBBC04', '#F47C34', '#EA4335']  # De verde a rojo
        self.label_probabilidad_enfermedad = [
            'Saludables', 'Leves rasgos', 'Rasgos considerables', 'Rasgos altos', 'Enfermas'
        ]

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

        # --- LÓGICA DE GRÁFICO INICIAL ---
        self.fig_bar = Figure(figsize=(8, 5), dpi=100)
        self.ax_bar = self.fig_bar.add_subplot(111)
        bottom = [0] * len(self.dates)  # Será [0]

        # Dibujar la barra placeholder
        values = [d[0] for d in self.data]  # Será [100.0]
        self.ax_bar.bar(self.dates, values, bottom=bottom, color=['#E0E0E0'], label='Esperando datos...')

        self.ax_bar.tick_params(axis='x', rotation=20, labelsize=8)
        self.ax_bar.set_ylabel('% del Total de diagnósticos')
        self.ax_bar.set_xlabel('Fecha de diagnóstico')
        self.ax_bar.legend(loc='upper right', fontsize=8)
        self.ax_bar.set_ylim(0, 100)
        self.canvas_bar = FigureCanvas(self.fig_bar)
        bar_chart_container.addWidget(self.canvas_bar)

        legend_layout = QHBoxLayout()
        for i, (color, label) in enumerate(zip(self.label_colores_prob_enfermedad, self.label_probabilidad_enfermedad)):
            lbl = QLabel(label)
            lbl.setStyleSheet(
                f"background-color: {color}; padding: 5px; border-radius: 5px; font-weight: bold; font-size: 14px")
            if i in [0, 1, 4]:  # Cambiar en blanco según el indice de los labels de colores
                lbl.setStyleSheet(
                    f"color: #F5F5F5; background-color: {color}; padding: 5px; border-radius: 5px; font-weight: bold; font-size: 14px")
            legend_layout.addWidget(lbl)
        bar_chart_container.addLayout(legend_layout)
        main_container.addLayout(bar_chart_container)

        # Gráfico de pastel
        pie_chart_container = QVBoxLayout()
        pie_title = QLabel("Último análisis")
        pie_title.setStyleSheet("font-weight: bold;")
        pie_chart_container.addWidget(pie_title)
        pie_data = [1]
        pie_labels = ['Esperando datos...']
        self.fig_pie = Figure(figsize=(5, 5), dpi=100)
        self.ax_pie = self.fig_pie.add_subplot(111)
        wedges, texts = self.ax_pie.pie(pie_data, labels=pie_labels, colors=['#E0E0E0'], startangle=90)
        self.ax_pie.axis('equal')
        self.canvas_pie = FigureCanvas(self.fig_pie)
        pie_chart_container.addWidget(self.canvas_pie)
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

    @pyqtSlot(dict, dict, dict)
    def set_result_plots(self, class_counts, list_results_per_class, list_leaf_state):
        """
        Este es el SLOT que recibe los datos de las clasificaciones.
        """
        self.list_results_per_class = list_results_per_class
        self.list_leaf_state = list_leaf_state

        # --- LÓGICA DE GRÁFICO DE PASTEL ---
        labels = list(self.list_leaf_state.keys())
        counts = list(self.list_leaf_state.values())
        colors_pie = self.label_colores_prob_enfermedad[:len(labels)]

        self.ax_pie.cla()
        labels_with_counts = [f'{l}\n{c}' for l, c in zip(labels, counts)]
        self.ax_pie.pie(counts, labels=labels_with_counts, colors=colors_pie, startangle=90, textprops={'fontsize': 9},
                        autopct='%1.1f%%')
        self.ax_pie.axis('equal')
        self.ax_pie.set_title("Resultados del Último Análisis")
        self.canvas_pie.draw()

        counts_raw = list(self.list_leaf_state.values())
        total_count = sum(counts_raw)

        if total_count == 0:
            counts_percent = [0.0] * len(counts_raw)
        else:
            counts_percent = [(c / total_count) * 100.0 for c in counts_raw]

        new_date = datetime.datetime.now().strftime("%d/%m/%y-%H:%M:%S")

        # 2. Reemplazar datos placeholder o añadir nuevos
        if self.data_is_placeholder:
            self.data = [counts_percent.copy()]
            self.dates = [new_date]
            self.data_is_placeholder = False
        else:
            self.data.append(counts_percent.copy())
            self.dates.append(new_date)

        # 3. Limpiar y redibujar el gráfico de barras
        self.ax_bar.cla()
        bottom = [0] * len(self.dates)

        # Iterar 5 veces (por las 5 categorías)
        for i in range(len(self.label_probabilidad_enfermedad)):
            # Seguridad si las listas no coinciden
            values = [d[i] if i < len(d) else 0 for d in self.data]

            self.ax_bar.bar(self.dates, values, bottom=bottom,
                            color=self.label_colores_prob_enfermedad[i],
                            label=self.label_probabilidad_enfermedad[i])
            bottom = [b + v for b, v in zip(bottom, values)]

        self.ax_bar.tick_params(axis='x', rotation=20, labelsize=8)
        self.ax_bar.set_ylabel('% del Total de diagnósticos')
        self.ax_bar.set_xlabel('Fecha de diagnóstico')
        self.ax_bar.legend(loc='upper right', fontsize=8)
        self.ax_bar.set_ylim(0, 100)
        self.canvas_bar.draw()


# --- PÁGINA DIAGNOSTICAR ---
class page_diagnosticar(QWidget):
    # Emisor de señales para comunicar en las otras clases los resultados
    diagnostico_completo = pyqtSignal(dict, dict, dict)

    # --- SEÑALES PARA WORKERS ---
    start_ssh = pyqtSignal(str)
    start_sftp_download = pyqtSignal(str, str)
    start_prediction = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.current_step = 0
        self.current_results = []
        self.class_file_lists = {}
        self.current_state_filename = {}
        self.conectado = False

        # Rutas para descarga y guardado (Ajusta según tu entorno local)
        project_root = os.path.dirname(os.path.realpath(__file__))
        self.PREDEFINED_SAVE_PATH = os.path.join(project_root, "fotos_path_pruebas_3")
        # Ruta remota en el UAV/Raspberry
        self.PREDEFINED_REMOTE_PATH = "/home/pera/Downloads/photos/photo_path"

        # Datos del plot
        self.counts = []
        self.label_colores_prob_enfermedad = ['#006A35', '#34A853', '#FBBC04', '#F47C34', '#EA4335']
        self.label_probabilidad_enfermedad = [
            'Saludables', 'Leves rasgos', 'Rasgos considerables', 'Rasgos altos', 'Enfermas'
        ]

        self.perimeter_points = []
        self.start_point = None

        # --- INICIALIZACIÓN DE WORKERS ---
        # 1. Worker SSH
        self.ssh_thread = QThread(self)
        self.ssh_worker = SshWorker()
        self.ssh_worker.moveToThread(self.ssh_thread)
        self.start_ssh.connect(self.ssh_worker.run_ssh_command)
        self.ssh_worker.progress.connect(self.on_ssh_progress)
        self.ssh_worker.finished.connect(self.on_ssh_finished)
        self.ssh_worker.error.connect(self.on_ssh_error)
        self.ssh_thread.start()

        # 2. Worker SFTP
        self.sftp_thread = QThread(self)
        self.sftp_worker = SftpWorker()
        self.sftp_worker.moveToThread(self.sftp_thread)
        self.start_sftp_download.connect(self.sftp_worker.download_files)
        self.sftp_worker.progress.connect(self.on_download_progress)
        self.sftp_worker.finished.connect(self.on_download_complete)
        self.sftp_worker.error.connect(self.on_download_error)
        self.sftp_thread.start()

        # 3. Worker Predicción
        self.prediction_thread = QThread(self)
        self.prediction_worker = PredictionWorker()
        self.prediction_worker.moveToThread(self.prediction_thread)
        self.start_prediction.connect(self.prediction_worker.run_prediction)
        self.prediction_worker.progress.connect(self.on_prediction_progress)
        self.prediction_worker.finished.connect(self.on_prediction_finished)
        self.prediction_worker.error.connect(self.on_prediction_error)
        self.prediction_thread.start()

        self.initUI()

    def initUI(self):
        self.layout = QVBoxLayout(self)
        self.stacked_widget = QStackedWidget()
        self.layout.addWidget(self.stacked_widget)

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
        self.page5 = self.create_page5()
        self.stacked_widget.addWidget(self.page5)

        self.update_page()

    # --- PÁGINAS DE INTERFAZ ---
    def create_page0(self):
        page = QWidget()
        self.page1_layout = QVBoxLayout(page)
        title = QLabel("Bienvenido al Sistema Detector de Enfermedades Foliares en cultivos de maíz")
        title.setStyleSheet("font-size: 26px; font-weight: bold; qproperty-alignment: 'AlignCenter';")
        self.page1_layout.addWidget(title)
        self.locked_label = QLabel("Conecta el UAV para comenzar.")
        self.locked_label.setStyleSheet("color: Black; font-size: 20px; font-weight: bold;")
        self.page1_layout.addWidget(self.locked_label)
        return page

    def create_page1(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("Paso 1: Punto de Despegue")
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(title)
        self.status_label1 = QLabel("Ubicación actual del UAV.")
        self.status_label1.setStyleSheet("color: green; font-size: 19px;")
        layout.addWidget(self.status_label1)
        self.coord_list_widget1 = QListWidget()
        self.coord_list_widget1.setMaximumHeight(20)
        layout.addWidget(self.coord_list_widget1)
        self.web_view1 = QWebEngineView()
        layout.addWidget(self.web_view1, 1)
        self.bridge1 = Bridge()
        self.channel1 = QWebChannel()
        self.channel1.registerObject("bridge", self.bridge1)
        self.web_view1.page().setWebChannel(self.channel1)
        self.web_view1.setHtml(self.get_map_html(), QUrl("qrc:///"))

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

    def create_page2(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("Paso 2: Área de monitoreo")
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(title)
        self.status_label2 = QLabel("Haz clic en el mapa para seleccionar los 4 puntos del perímetro.")
        self.status_label2.setStyleSheet("color: green; font-size: 19px")
        layout.addWidget(self.status_label2)
        self.coord_list_widget2 = QListWidget()
        self.coord_list_widget2.setMaximumHeight(80)
        layout.addWidget(self.coord_list_widget2)
        self.web_view2 = QWebEngineView()
        layout.addWidget(self.web_view2, 1)
        self.bridge2 = Bridge()
        self.channel2 = QWebChannel()
        self.channel2.registerObject("bridge", self.bridge2)
        self.web_view2.page().setWebChannel(self.channel2)
        self.bridge2.mapClicked.connect(self.handle_perimeter_map_click)
        self.web_view2.setHtml(self.get_map_html(), QUrl("qrc:///"))

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
        title = QLabel("Paso 3: Realizando diagnostico!")
        title.setStyleSheet("font-size: 26px; font-weight: bold;")
        layout.addWidget(title)

        # Variables visuales para actualizar en los slots
        self.p3_status_label = QLabel("El UAV se encuentra en movimiento.")
        self.p3_status_label.setStyleSheet(
            "color: Black; font-size: 20px; font-weight: bold; qproperty-alignment: 'AlignCenter';")
        self.p3_percent_label = QLabel("0% monitoreado")
        self.p3_percent_label.setStyleSheet("font-size: 16px;")

        layout.addWidget(self.p3_percent_label)
        layout.addWidget(self.p3_status_label)

        btn_abortar = QPushButton("Abortar operación")
        btn_abortar.setStyleSheet("background-color: #f44336; color: white;")
        btn_abortar.clicked.connect(self.abort)

        # Botón Siguiente (deshabilitado inicialmente)
        self.btn_page3_siguiente = QPushButton("Siguiente")
        self.btn_page3_siguiente.setStyleSheet("background-color: #4CAF50; color: white;")
        self.btn_page3_siguiente.clicked.connect(self.go_to_step4)
        self.btn_page3_siguiente.setEnabled(False)

        btn_layout = QHBoxLayout()
        btn_layout.addWidget(btn_abortar)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_page3_siguiente)
        layout.addLayout(btn_layout)
        return page

    def create_page4(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel("Paso 4: Obtención de resultados")
        title.setStyleSheet("font-size: 26px; font-weight: bold;")
        layout.addWidget(title)

        self.locked_label = QLabel("Reacopilando datos del UAV.")
        self.locked_label.setStyleSheet(
            "color: Black; font-size: 20px; font-weight: bold; qproperty-alignment: 'AlignCenter';")
        self.text = QLabel("Presione el botón para comenzar el procesamiento de la imagenes")
        self.text.setStyleSheet("font-size: 22px;")

        layout.addWidget(self.text)
        layout.addWidget(self.locked_label)

        btn_abortar = QPushButton("Abortar procesamiento")
        btn_abortar.setStyleSheet("background-color: #f44336; color: white;")
        btn_abortar.clicked.connect(self.abort)

        self.btn_ejecutar = QPushButton("Ejecutar procesamiento")
        self.btn_ejecutar.setStyleSheet("background-color: #8EC5FF; color: Black;")
        # CONECTAMOS A LA LÓGICA DE HILOS (DESCARGA + PREDICCION)
        self.btn_ejecutar.clicked.connect(self.start_download_and_predict)

        self.btn_siguiente = QPushButton("Siguiente")
        self.btn_siguiente.setStyleSheet("background-color: #4CAF50; color: white;")
        self.btn_siguiente.clicked.connect(self.go_to_step5)
        self.btn_siguiente.hide()

        btn_layout = QHBoxLayout()
        btn_layout.addWidget(btn_abortar)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_ejecutar)
        btn_layout.addWidget(self.btn_siguiente)
        layout.addLayout(btn_layout)
        return page

    def create_page5(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)

        # --- Títulos ---
        title = QLabel("¡Diagnóstico Finalizado!")
        title.setStyleSheet("font-size: 24px; font-weight: bold; color: green;")
        layout.addWidget(title)

        fecha = datetime.datetime.today().strftime("%d/%m/%y-%H:%M")
        subtitle = QLabel("Resultados de diagnóstico - " + fecha + " hrs.")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("font-size: 14px; color: #555;")
        layout.addWidget(subtitle)

        # --- Tarjeta Principal ---
        content_card = QFrame()
        content_card.setObjectName("contentCard")
        content_card.setFrameShape(QFrame.StyledPanel)
        content_layout = QHBoxLayout(content_card)

        # --- Mapas ---
        map_frame = QFrame()
        map_layout = QVBoxLayout(map_frame)
        map_layout.setContentsMargins(0, 0, 0, 0)

        self.web_view4 = QWebEngineView()
        self.bridge4 = Bridge()
        self.channel4 = QWebChannel()
        self.channel4.registerObject("bridge", self.bridge4)
        self.web_view4.page().setWebChannel(self.channel4)
        self.web_view4.setHtml(self.get_map_html(), QUrl("qrc:///"))
        map_layout.addWidget(self.web_view4)

        self.web_view4_2 = QWebEngineView()
        self.bridge4_2 = Bridge()
        self.channel4_2 = QWebChannel()
        self.channel4_2.registerObject("bridge", self.bridge4_2)
        self.web_view4_2.page().setWebChannel(self.channel4_2)
        self.web_view4_2.setHtml(self.get_map_html(), QUrl("qrc:///"))
        map_layout.addWidget(self.web_view4_2)

        content_layout.addWidget(map_frame, 2)

        # --- Panel Derecho ---
        right_panel_layout = QVBoxLayout()
        right_panel_layout.setSpacing(15)

        # Gráfico pastel
        fig = Figure(figsize=(5, 4), dpi=100)
        fig.patch.set_alpha(0.0)
        self.ax = fig.add_subplot(111)
        self.ax.set_title('Clasificación del total de fotos tomadas', fontsize=12)
        self.ax.pie([1], labels=['Esperando...'], colors=['#E0E0E0'], startangle=90)  # Placeholder
        self.ax.axis('equal')
        self.canvas_pie_5 = FigureCanvas(fig)
        self.canvas_pie_5.setStyleSheet("background-color: transparent;")
        right_panel_layout.addWidget(self.canvas_pie_5)

        # Gráfico barras
        self.fig_bar = Figure(figsize=(5, 3), dpi=100)
        self.fig_bar.patch.set_alpha(0.0)
        self.ax_bar = self.fig_bar.add_subplot(111)
        self.ax_bar.set_title('Conteo por Enfermedad', fontsize=10)
        self.ax_bar.bar(['Esperando'], [0], color='#E0E0E0')
        self.ax_bar.set_ylabel('Imágenes')
        self.fig_bar.tight_layout()
        self.canvas_bar_5 = FigureCanvas(self.fig_bar)
        self.canvas_bar_5.setStyleSheet("background-color: transparent;")
        right_panel_layout.addWidget(self.canvas_bar_5)

        # Botones
        btn_layout = QVBoxLayout()
        btn_guardar = QPushButton(" Guardar")
        btn_guardar.setIcon(QIcon.fromTheme("document-save"))
        btn_ver_fotos = QPushButton("Ver galería")
        btn_ver_fotos.setIcon(QIcon.fromTheme("camera-photo"))
        btn_terminar = QPushButton("Terminar")
        btn_terminar.setStyleSheet("background-color: #4CAF50; color: white;")

        btn_guardar.clicked.connect(self.guardar_diagnostico)
        btn_ver_fotos.clicked.connect(self.abrir_nav)
        btn_terminar.clicked.connect(self.reset_diagnostic_ended)

        btn_layout.addWidget(btn_guardar)
        btn_layout.addWidget(btn_ver_fotos)
        btn_layout.addSpacing(10)
        btn_layout.addWidget(btn_terminar)

        right_panel_layout.addLayout(btn_layout)
        right_panel_layout.addStretch()
        content_layout.addLayout(right_panel_layout, 1)
        layout.addWidget(content_card)

        # --- Barra de Estado ---
        status_bar_frame = QFrame()
        status_bar_frame.setObjectName("statusBar")
        status_bar_frame.setFrameShape(QFrame.StyledPanel)
        status_bar_frame.setMinimumHeight(65)
        status_bar_layout = QHBoxLayout(status_bar_frame)
        status_bar_layout.setContentsMargins(20, 5, 20, 5)
        status_bar_layout.setSpacing(25)

        self.label_ha = QLabel("Área: 1 ha.")

        # Elementos barra estado (simulados)
        # Sensores
        item1 = QHBoxLayout()
        item1.addWidget(QLabel("Sensores"))
        lbl_s = QLabel("Buen estado");
        lbl_s.setStyleSheet("font-weight: bold;")
        item1.addWidget(lbl_s)
        status_bar_layout.addLayout(item1)
        # Bateria
        item2 = QHBoxLayout()
        item2.addWidget(QLabel("Batería"))
        lbl_b = QLabel("65%");
        lbl_b.setStyleSheet("font-weight: bold;")
        item2.addWidget(lbl_b)
        status_bar_layout.addLayout(item2)

        status_bar_layout.addStretch()
        status_bar_layout.addWidget(self.label_ha)

        layout.addWidget(status_bar_frame)
        return page

    def get_map_html(self):
        """ Código HTML/JS para el mapa Leaflet (Versión utils_2.py mejorada) """
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
            <style> body { margin: 0; padding: 0; } #map { height: 100vh; width: 100%; } </style>
        </head>
        <body>
            <div id="map"></div>
            <script>
                var map = L.map('map').setView([20.432939, -99.598862], 18);
                L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
                    attribution: 'mapa interactuable', maxNativeZoom: 18, maxZoom: 22
                }).addTo(map);
                var markerLayer = L.layerGroup().addTo(map);
                var polygonLayer = L.layerGroup().addTo(map);
                var pythonBridge; 
                new QWebChannel(qt.webChannelTransport, function(channel) {
                    pythonBridge = channel.objects.bridge;
                });
                map.on('click', function(e) {
                    if (pythonBridge) { pythonBridge.onMapClicked(e.latlng.lat, e.latlng.lng); }
                });
                function addLocationMarker(lat, lng, color) {
                    L.circleMarker([lat, lng], { radius: 2, color: color, fillColor: color, fillOpacity: 0.8 })
                    .bindPopup("Ubicación actual").addTo(markerLayer);
                }
                function addMarker(lat, lng, color) {
                    L.circleMarker([lat, lng], { radius: 2, color: color, fillColor: color, fillOpacity: 0.8 }).addTo(markerLayer);
                }
                function addStateMark(lat, lng, color, clase) {
                    var text = `${clase}<br>Lat: ${lat.toFixed(5)}<br>Lng: ${lng.toFixed(5)}`;
                    L.circleMarker([lat, lng], { radius: 30, color: color, fillColor: color, fillOpacity: 0.6 })
                    .bindPopup(text).addTo(markerLayer);
                }
                function drawPolygon(points_json) {
                    polygonLayer.clearLayers();
                    var points = JSON.parse(points_json);
                    if (points && points.length >= 3) {
                        L.polygon(points, { color: 'red', weight: 2, fillColor: '#ff0000', fillOpacity: 0.2 }).addTo(polygonLayer);
                    }
                }
                function drawLastPolygon(points_json) {
                    polygonLayer.clearLayers();
                    var points = JSON.parse(points_json);
                    L.polygon(points, { color: '#5D6D7E', weight: 2, fillColor: '#D6DBDF', fillOpacity: 0.2 }).addTo(polygonLayer);
                }
                function clearMarkers() { markerLayer.clearLayers(); polygonLayer.clearLayers(); }
            </script>
        </body>
        </html>
        """

    @pyqtSlot(bool)
    def set_estado_conexion(self, conectado):
        self.conectado = conectado
        self.go_to_step1()

    # --- MANEJADORES MAPA ---
    @pyqtSlot(float, float)
    def handle_start_point_map_click(self, lat, lng):
        if self.start_point is not None:
            item_text = f"Punto de inicio: ({lat:.5f}, {lng:.5f})"
            self.coord_list_widget1.addItem(item_text)
            self.web_view1.page().runJavaScript(f"addLocationMarker({lat}, {lng}, 'lightgreen');")
            self.status_label1.setText("Ubicación actual del UAV.")
        else:
            self.status_label1.setText("Solo se puede seleccionar 1 punto. Limpie para reiniciar.")

    @pyqtSlot(float, float)
    def handle_perimeter_map_click(self, lat, lng):
        if len(self.perimeter_points) < 4:
            self.perimeter_points.append((lat, lng))
            item_text = f"Punto {len(self.perimeter_points)}: ({lat:.5f}, {lng:.5f})"
            self.coord_list_widget2.addItem(item_text)
            self.web_view2.page().runJavaScript(f"addMarker({lat}, {lng}, 'red');")
            if len(self.perimeter_points) >= 3:
                points_json = json.dumps(self.perimeter_points)
                self.web_view2.page().runJavaScript(f"drawPolygon('{points_json}');")
            if len(self.perimeter_points) == 4:
                self.status_label2.setText("Perímetro de 4 puntos seleccionado.")
        else:
            self.status_label2.setText("Máximo de 4 puntos alcanzado. Limpie para reiniciar.")

    def distance(self, punto1, punto2):
        if not punto1 or not punto2: return 0.0
        return geodesic(punto1, punto2).meters

    def calcular_hectarea(self):
        if len(self.perimeter_points) < 3: return 0.0
        geod = Geod(ellps='WGS84')
        puntos_poligono = self.perimeter_points + [self.perimeter_points[0]]
        puntos_lon_lat = [(lon, lat) for lat, lon in puntos_poligono]
        polygon = Polygon(puntos_lon_lat)
        area, perimetro = geod.geometry_area_perimeter(polygon)
        return abs(area * 0.0001)  # Conversión a hectáreas

    # --- LIMPIEZA Y ACTUALIZACIÓN ---
    def clear_start_point_marker(self):
        self.start_point = (20.432939, -99.598862)
        self.coord_list_widget1.clear()
        self.web_view1.page().runJavaScript("clearMarkers();")
        self.status_label1.setText("Ubicación actual del UAV.")

    def up_to_date_map1(self):
        self.web_view1.page().runJavaScript("clearMarkers();")
        self.web_view1.page().runJavaScript(
            f"addLocationMarker({self.start_point[0]}, {self.start_point[1]}, 'lightgreen');")

    def up_to_date_map2(self):
        self.web_view2.page().runJavaScript("clearMarkers();")
        self.web_view2.page().runJavaScript(
            f"addLocationMarker({self.start_point[0]}, {self.start_point[1]}, 'lightgreen');")
        for lat, lng in self.perimeter_points:
            self.web_view2.page().runJavaScript(f"addMarker({lat}, {lng}, 'red');")
        else:
            points_json = json.dumps(self.perimeter_points)
            self.web_view2.page().runJavaScript(f"drawPolygon('{points_json}');")

    def clear_perimeter_markers(self):
        self.perimeter_points = []
        self.coord_list_widget2.clear()
        self.web_view2.page().runJavaScript("clearMarkers();")
        self.web_view2.page().runJavaScript(
            f"addLocationMarker({self.start_point[0]}, {self.start_point[1]}, 'lightgreen');")
        self.status_label2.setText("Haz clic en el mapa para seleccionar los 4 puntos del perímetro.")

    # --- NAVEGACIÓN (STEPS) ---
    def go_to_step1(self):
        if self.conectado:
            self.current_step = 1
            self.coordenadas_iniciales = (20.432939, -99.598862)
            lat, lng = self.coordenadas_iniciales
            self.start_point = (lat, lng)
            zoom = 18
            self.web_view1.page().runJavaScript(f"map.setView([{lat}, {lng}], {zoom});")
            self.web_view1.page().runJavaScript(f"addLocationMarker({lat}, {lng}, 'lightgreen');")
            self.handle_start_point_map_click(lat, lng)
            self.update_page()

    def go_to_step2(self):
        if self.start_point is not None:
            self.current_step = 2
            lat, lng = self.start_point
            zoom = 18
            self.web_view2.page().runJavaScript(f"map.setView([{lat}, {lng}], {zoom});")
            self.web_view2.page().runJavaScript(f"addLocationMarker({lat}, {lng}, 'lightgreen');")
            self.update_page()
        else:
            QMessageBox.warning(self, "Error", "Debes seleccionar un punto de despegue.")

    def go_to_step3(self):
        # --- MODIFICADO: LÓGICA CON SSH ---
        if len(self.perimeter_points) != 4:
            QMessageBox.warning(self, "Error", "Debes seleccionar 4 puntos.")
            return

        hect = self.calcular_hectarea()
        if hect > 1.00:
            QMessageBox.warning(self, "Error", f'{hect:.3f} ha seleccionados.\nSolo se permite 1.00 ha como máximo')
            return

        self.current_step = 3
        self.update_page()

        # Reset labels UI
        self.p3_status_label.setText("Iniciando conexión con el UAV...")
        self.p3_percent_label.setText("0% monitoreado")
        self.btn_page3_siguiente.setEnabled(False)

        # Iniciar SSH Worker
        json_payload = json.dumps(self.perimeter_points)
        self.start_ssh.emit(json_payload)

    def go_to_step4(self):
        self.current_step = 4
        self.label_ha.setText("Área monitoreado:" + f'{self.calcular_hectarea():.3f}' + "ha.")
        self.update_page()
        # Reset UI step 4
        self.locked_label.setText("Listo para descargar y procesar.")
        self.text.setText(f"Se descargarán fotos de: {self.PREDEFINED_REMOTE_PATH}")
        self.btn_ejecutar.setEnabled(True)
        self.btn_ejecutar.show()
        self.btn_siguiente.hide()

    def go_to_step5(self):
        if (len(self.perimeter_points) == 4) and (self.current_results is not None):
            self.current_step = 5
            # Mapa 1
            self.web_view4.page().runJavaScript("clearMarkers();")
            self.web_view4.page().runJavaScript(f"map.setView([{self.start_point[0]}, {self.start_point[1]}], {20});")
            self.web_view4.page().runJavaScript(
                f"addLocationMarker({self.start_point[0]}, {self.start_point[1]}, 'lightgreen');")

            points_json = json.dumps(self.perimeter_points)
            self.web_view4.page().runJavaScript(f"drawLastPolygon('{points_json}');")
            for p in self.perimeter_points:
                self.web_view4.page().runJavaScript(f"addMarker({p[0]}, {p[1]}, '#5D6D7E');")

            for i, estado in enumerate(self.current_state_filename.keys()):
                color = self.label_colores_prob_enfermedad[i]
                for coord in self.current_state_filename[estado]:
                    self.web_view4.page().runJavaScript(f"addStateMark({coord[0]}, {coord[1]}, '{color}', '{estado}');")

            # Mapa 2
            self.web_view4_2.page().runJavaScript("clearMarkers();")
            self.web_view4_2.page().runJavaScript(f"map.setView([{self.start_point[0]}, {self.start_point[1]}], {20});")
            self.web_view4_2.page().runJavaScript(f"drawLastPolygon('{points_json}');")

            for i, estado in enumerate(self.class_file_lists.keys()):
                color = ['#A6A09B', '#FF8904', '#006A35', '#795548', '#EA4335'][i]
                for coord in self.class_file_lists[estado]:
                    self.web_view4_2.page().runJavaScript(
                        f"addStateMark({coord[0]}, {coord[1]}, '{color}', '{estado}');")

            # Plots
            if len(self.counts) != 0:
                self.ax.cla()
                labels_data = [f'{l}\n{s}' for l, s in zip(self.label_probabilidad_enfermedad, self.counts)]
                self.ax.pie(self.counts, labels=labels_data, colors=self.label_colores_prob_enfermedad, startangle=90,
                            textprops={'fontsize': 9})
                self.ax.axis('equal')
                self.ax.set_title('Clasificación del total de fotos tomadas', fontsize=12)
                self.canvas_pie_5.draw()

            if self.current_results:
                self.ax_bar.cla()
                enfermedades = list(self.current_results.keys())  # Ojo, current_results aqui debe ser class_counts
                conteos = list(self.current_results.values())
                colores_barras = ['#A6A09B', '#FF8904', '#006A35', '#795548', '#EA4335']
                barras = self.ax_bar.bar(enfermedades, conteos, color=colores_barras[:len(enfermedades)])
                self.ax_bar.set_title('Detecciones por Clase', fontsize=10)
                self.ax_bar.tick_params(axis='x', labelrotation=45, labelsize=8)
                self.fig_bar.tight_layout()
                self.canvas_bar_5.draw()

            self.update_page()
        else:
            QMessageBox.warning(self, "Error", "Debes seleccionar exactamente 4 puntos.")

    def guardar_diagnostico(self):
        fecha_hora = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"diagnostico_mapa_{fecha_hora}.png"
        filename_2 = f"diagnostico_enfermedades_{fecha_hora}.png"
        try:
            os.makedirs("./diagnosticos_guardados/", exist_ok=True)
            self.web_view4.grab().save(f"./diagnosticos_guardados/{filename}", "PNG")
            self.web_view4_2.grab().save(f"./diagnosticos_guardados/{filename_2}", "PNG")
            QMessageBox.information(self, "Guardado Exitoso", f"Mapas guardados: \n{filename}\n{filename_2}")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Error al guardar: {e}")

    def come_back_to_step1(self):
        self.clear_perimeter_markers()
        self.current_step = 1
        self.update_page()

    def abort(self):
        self.current_step = 4
        self.update_page()

    def reset_diagnostic_ended(self):
        self.clear_perimeter_markers()
        self.clear_start_point_marker()
        self.counts = []
        self.current_results = []
        self.current_step = 0
        self.btn_ejecutar.show()
        self.btn_siguiente.hide()
        self.btn_ejecutar.setText("Ejecutar procesamiento")
        self.btn_ejecutar.setEnabled(True)
        QMessageBox.information(self, "Monitoreo finalizado", "Puede ver los resultados en la opción <b>Tablero</b>")
        self.go_to_step1()

    def update_page(self):
        self.stacked_widget.setCurrentIndex(self.current_step)

    def abrir_nav(self):
        path = os.path.abspath("./diagnosticos_guardados/")
        os.system(f'xdg-open "{path}"')

    # --- LÓGICA DE WORKERS Y SLOTS (INTEGRADA) ---

    def start_download_and_predict(self):
        """ Inicia la secuencia: Descarga -> Predicción """
        self.btn_ejecutar.setText("Descargando...")
        self.btn_ejecutar.setEnabled(False)
        self.locked_label.setText("Iniciando descarga de fotos...")
        QApplication.processEvents()

        try:
            os.makedirs(self.PREDEFINED_SAVE_PATH, exist_ok=True)
            self.start_sftp_download.emit(self.PREDEFINED_REMOTE_PATH, self.PREDEFINED_SAVE_PATH)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Error creando carpeta local: {e}")
            self.btn_ejecutar.setText("Error")

    # Slots SSH (Paso 3)
    @pyqtSlot(str)
    def on_ssh_progress(self, message):
        if "%" in message:
            self.p3_percent_label.setText(message)
        else:
            self.p3_status_label.setText(message)

    @pyqtSlot(str, str)
    def on_ssh_finished(self, out, err):
        self.p3_status_label.setText("¡Vuelo completado!")
        self.p3_percent_label.setText("100% monitoreado")
        self.btn_page3_siguiente.setEnabled(True)
        if err: print("SSH Error Log:", err)

    @pyqtSlot(str)
    def on_ssh_error(self, error_message):
        QMessageBox.critical(self, "Error de Vuelo", error_message)
        self.come_back_to_step1()

    # Slots SFTP (Paso 4)
    @pyqtSlot(str)
    def on_download_progress(self, message):
        self.locked_label.setText("Descargando...")
        self.text.setText(message)

    @pyqtSlot()
    def on_download_complete(self):
        self.locked_label.setText("Descarga completa. Iniciando IA...")
        self.text.setText("Cargando modelo...")
        # Iniciar IA
        self.start_prediction.emit(self.PREDEFINED_SAVE_PATH)

    @pyqtSlot(str)
    def on_download_error(self, error_message):
        QMessageBox.critical(self, "Error Descarga", error_message)
        self.btn_ejecutar.setText("Ejecutar procesamiento")
        self.btn_ejecutar.setEnabled(True)

    # Slots Predicción (Paso 4 -> 5)
    @pyqtSlot(str)
    def on_prediction_progress(self, message):
        self.locked_label.setText("Procesando con IA...")
        self.text.setText(message)

    @pyqtSlot(dict, dict, dict, dict, list)
    def on_prediction_finished(self, class_counts, class_file_lists, class_leaf_state, state_file_lists,
                               nuevos_conteos):
        self.locked_label.setText("¡Procesamiento finalizado!")
        self.text.setText("Resultados listos.")

        # Guardar resultados en variables de clase para usarlos en go_to_step5
        # current_results guardará class_counts para el gráfico de barras
        self.current_results = class_counts
        self.class_file_lists = class_file_lists
        self.current_state_filename = state_file_lists
        self.counts = nuevos_conteos

        # Emitir señal al tablero
        self.diagnostico_completo.emit(class_counts, class_file_lists, class_leaf_state)

        self.btn_ejecutar.hide()
        self.btn_siguiente.show()

    @pyqtSlot(str)
    def on_prediction_error(self, error_message):
        QMessageBox.critical(self, "Error IA", error_message)
        self.btn_ejecutar.setText("Ejecutar procesamiento")
        self.btn_ejecutar.setEnabled(True)


# --- PÁGINA ESTADÍSTICAS (BASE: utils_2.py) ---
class page_Estadisticas(QWidget):
    def __init__(self, parent=None):
        super(page_Estadisticas, self).__init__(parent)

        # Datos actuales
        self.class_counts = {}
        self.list_leaf_state = {}

        # Historial para la gráfica de evolución
        self.history_dates = []
        self.history_data = []

        # Configuración del visor
        self.all_image_files = []
        self.current_pixmap = QPixmap()
        self.current_selected_date_str = None
        self.current_selected_type = "mapa"
        self.scan_dir = "./diagnosticos_guardados/"

        # Colores
        self.colores_enfermedad = ['#A6A09B', '#FF8904', '#006A35', '#795548', '#EA4335']
        self.colores_salud = ['#006A35', '#34A853', '#FBBC04', '#F47C34', '#EA4335']
        self.labels_salud = ['Saludables', 'Leves rasgos', 'Rasgos considerables', 'Rasgos altos', 'Enfermas']

        self.initUI()
        self.refresh_date_list()

    def initUI(self):
        self.setWindowTitle("Visor de Reportes y Estadísticas")
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        title_label = QLabel("Centro de Resultados")
        title_label.setStyleSheet("font-size: 20px; font-weight: bold;")
        layout.addWidget(title_label)

        main_container = QHBoxLayout()

        # --- IZQUIERDA ---
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        radio_layout = QHBoxLayout()
        self.radio_group = QButtonGroup(self)
        self.rb_mapa = QRadioButton("Mapa de Calor (Img)")
        self.rb_clases = QRadioButton("Enfermedades (Img)")
        self.rb_graficas = QRadioButton("Gráficas en Vivo")

        radio_layout.addWidget(self.rb_mapa)
        radio_layout.addWidget(self.rb_clases)
        radio_layout.addWidget(self.rb_graficas)
        radio_layout.addStretch()

        self.radio_group.addButton(self.rb_mapa)
        self.radio_group.addButton(self.rb_clases)
        self.radio_group.addButton(self.rb_graficas)

        self.rb_mapa.toggled.connect(self.on_radio_toggled)
        self.rb_clases.toggled.connect(self.on_radio_toggled)
        self.rb_graficas.toggled.connect(self.on_radio_toggled)

        left_layout.addLayout(radio_layout)

        self.central_stack = QStackedWidget()

        self.image_label = QLabel("Seleccione una fecha para ver el reporte.")
        self.image_label.setScaledContents(True)
        self.image_label.setStyleSheet("border: 1px solid #CCC; background: #F0F0F0; border-radius: 5px;")
        self.central_stack.addWidget(self.image_label)

        self.plots_widget = QWidget()
        plots_layout = QHBoxLayout(self.plots_widget)

        self.fig1 = Figure(figsize=(4, 3), dpi=80);
        self.ax1 = self.fig1.add_subplot(111)
        self.canvas1 = FigureCanvas(self.fig1);
        plots_layout.addWidget(self.canvas1)

        self.fig2 = Figure(figsize=(4, 3), dpi=80);
        self.ax2 = self.fig2.add_subplot(111)
        self.canvas2 = FigureCanvas(self.fig2);
        plots_layout.addWidget(self.canvas2)

        self.fig3 = Figure(figsize=(4, 3), dpi=80);
        self.ax3 = self.fig3.add_subplot(111)
        self.canvas3 = FigureCanvas(self.fig3);
        plots_layout.addWidget(self.canvas3)

        self.central_stack.addWidget(self.plots_widget)
        left_layout.addWidget(self.central_stack, 1)

        button_layout = QHBoxLayout()
        self.btn_email = QPushButton("Enviar Email")
        self.btn_pdf = QPushButton("Convertir a PDF")
        self.btn_gallery = QPushButton("Abrir Galería")

        self.btn_email.setIcon(QIcon.fromTheme("mail-send"))
        self.btn_pdf.setIcon(QIcon.fromTheme("document-export"))
        self.btn_gallery.setIcon(QIcon.fromTheme("folder-pictures"))

        button_layout.addStretch()
        button_layout.addWidget(self.btn_email)
        button_layout.addWidget(self.btn_pdf)
        button_layout.addWidget(self.btn_gallery)
        button_layout.addStretch()

        left_layout.addLayout(button_layout)

        self.btn_email.clicked.connect(self.on_send_email)
        self.btn_pdf.clicked.connect(self.on_convert_pdf)
        self.btn_gallery.clicked.connect(self.on_open_gallery)

        main_container.addWidget(left_panel, 3)

        # --- DERECHA ---
        right_panel = QWidget()
        right_panel.setMaximumWidth(220)
        right_layout = QVBoxLayout(right_panel)

        list_title = QLabel("Historial de Operaciones")
        list_title.setStyleSheet("font-weight: bold; font-size: 14px;")
        right_layout.addWidget(list_title)

        self.date_list_widget = QListWidget()
        self.date_list_widget.itemClicked.connect(self.on_date_selected)
        right_layout.addWidget(self.date_list_widget)

        main_container.addWidget(right_panel, 1)
        layout.addLayout(main_container)

        self.rb_mapa.setChecked(True)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_date_list()
        if self.rb_graficas.isChecked():
            self.update_live_plots()

    @pyqtSlot(dict, dict, dict)
    def set_result_plots(self, class_counts, list_results_per_class, list_leaf_state):
        self.class_counts = class_counts
        self.list_leaf_state = list_leaf_state

        fecha_hora = datetime.datetime.now().strftime("%H:%M\n%d/%m")
        self.history_dates.append(fecha_hora)

        counts_raw = [list_leaf_state.get(lbl, 0) for lbl in self.labels_salud]
        total = sum(counts_raw)
        if total > 0:
            percents = [(c / total) * 100 for c in counts_raw]
        else:
            percents = [0] * 5

        if not self.history_data:
            self.history_data = [[p] for p in percents]
        else:
            for i, p in enumerate(percents):
                self.history_data[i].append(p)

        if len(self.history_dates) > 10:
            self.history_dates.pop(0)
            for l in self.history_data: l.pop(0)

        self.refresh_date_list()
        if self.rb_graficas.isChecked(): self.update_live_plots()

    def update_live_plots(self):
        self.ax1.cla()
        if self.class_counts:
            labels = list(self.class_counts.keys())
            values = list(self.class_counts.values())
            short_labels = [l.replace('_', '\n') for l in labels]
            bars = self.ax1.bar(short_labels, values, color=self.colores_enfermedad[:len(labels)])
            self.ax1.set_title("Detecciones por Clase", fontsize=9, fontweight='bold')
            self.ax1.tick_params(axis='x', labelsize=7)
            for bar in bars:
                h = bar.get_height()
                self.ax1.text(bar.get_x() + bar.get_width() / 2, h, str(int(h)), ha='center', va='bottom', fontsize=8)
        else:
            self.ax1.text(0.5, 0.5, "Sin datos", ha='center')
        self.fig1.tight_layout();
        self.canvas1.draw()

        self.ax2.cla()
        if self.list_leaf_state:
            counts = [self.list_leaf_state.get(l, 0) for l in self.labels_salud]
            short_labels_salud = ["Saludable", "Leve", "Consid.", "Alto", "Enferma"]
            bars2 = self.ax2.bar(short_labels_salud, counts, color=self.colores_salud)
            self.ax2.set_title("Niveles de Salud", fontsize=9, fontweight='bold')
            self.ax2.tick_params(axis='x', labelsize=7)
            for bar in bars2:
                h = bar.get_height()
                self.ax2.text(bar.get_x() + bar.get_width() / 2, h, str(int(h)), ha='center', va='bottom', fontsize=8)
        else:
            self.ax2.text(0.5, 0.5, "Sin datos", ha='center')
        self.fig2.tight_layout();
        self.canvas2.draw()

        self.ax3.cla()
        if self.history_dates:
            bottom = [0] * len(self.history_dates)
            for i, category_data in enumerate(self.history_data):
                self.ax3.bar(self.history_dates, category_data, bottom=bottom,
                             color=self.colores_salud[i], label=self.labels_salud[i], width=0.6)
                bottom = [b + v for b, v in zip(bottom, category_data)]
            self.ax3.set_title("Evolución Histórica (%)", fontsize=9, fontweight='bold')
            self.ax3.set_ylim(0, 100)
            self.ax3.tick_params(axis='x', labelsize=7)
            self.ax3.legend(loc='upper center', bbox_to_anchor=(0.5, -0.15), ncol=2, fontsize=6, frameon=False)
        else:
            self.ax3.text(0.5, 0.5, "Sin historial", ha='center')
        self.fig3.tight_layout();
        self.canvas3.draw()

    @pyqtSlot()
    def on_radio_toggled(self):
        if self.rb_graficas.isChecked():
            self.central_stack.setCurrentIndex(1)
            self.update_live_plots()
            self.current_selected_type = "graficas"
            self.date_list_widget.clearSelection()
        else:
            self.central_stack.setCurrentIndex(0)
            if self.rb_mapa.isChecked():
                self.current_selected_type = "mapa"
            elif self.rb_clases.isChecked():
                self.current_selected_type = "enfermedades"
            if self.current_selected_date_str:
                self.update_image_viewer()
            else:
                self.image_label.setText("Seleccione una fecha.")

    @pyqtSlot()
    def refresh_date_list(self):
        self.date_list_widget.clear()
        if not os.path.exists(self.scan_dir): os.makedirs(self.scan_dir)
        self.all_image_files = os.listdir(self.scan_dir)
        unique_dates = set()
        for f in self.all_image_files:
            if f.startswith("diagnostico_") and f.endswith(".png"):
                parts = f.split('_')
                if len(parts) >= 4:
                    date_part = parts[2]
                    time_part = parts[3].replace(".png", "")
                    full_timestamp = date_part + time_part
                    if date_part.isdigit() and len(date_part) == 8:
                        unique_dates.add(full_timestamp)

        if not unique_dates:
            self.date_list_widget.addItem("No hay reportes guardados")
            return

        for ts in sorted(list(unique_dates), reverse=True):
            try:
                fmt_date = f"{ts[6:8]}/{ts[4:6]}/{ts[0:4]} - {ts[8:10]}:{ts[10:12]}"
            except:
                fmt_date = ts
            item = QListWidgetItem(fmt_date)
            item.setData(Qt.UserRole, ts)
            self.date_list_widget.addItem(item)

    @pyqtSlot(QListWidgetItem)
    def on_date_selected(self, item):
        if self.rb_graficas.isChecked(): return
        if item and item.data(Qt.UserRole):
            self.current_selected_date_str = item.data(Qt.UserRole)
            self.update_image_viewer()

    def update_image_viewer(self):
        if not self.current_selected_date_str: return
        date_part = self.current_selected_date_str[0:8]
        time_part = self.current_selected_date_str[8:]
        suffix = f"_{date_part}_{time_part}.png"
        target_file = f"diagnostico_{self.current_selected_type}{suffix}"
        full_path = os.path.join(self.scan_dir, target_file)

        if os.path.exists(full_path):
            self.current_pixmap = QPixmap(full_path)
            self.image_label.setPixmap(self.current_pixmap)
        else:
            self.current_pixmap = QPixmap()
            self.image_label.setPixmap(QPixmap())
            self.image_label.setText(f"No se encontró:\n{target_file}")

    def guardar_plots_para_pdf(self):
        output_dir = "./diagnosticos_guardados/"
        if not os.path.exists(output_dir): os.makedirs(output_dir)
        time_d = self.current_selected_date_str[0:8]
        time_h = self.current_selected_date_str[8:]
        timestamp = time_d + "_" + time_h
        rutas = {}
        try:
            if hasattr(self, 'fig1'):
                path1 = os.path.abspath(os.path.join(output_dir, f"plot_clases_{timestamp}.png"))
                self.fig1.savefig(path1, bbox_inches='tight', dpi=100, transparent=True)
                rutas['clases'] = path1
            if hasattr(self, 'fig2'):
                path2 = os.path.abspath(os.path.join(output_dir, f"plot_enfermedades_{timestamp}.png"))
                self.fig2.savefig(path2, bbox_inches='tight', dpi=100, transparent=True)
                rutas['salud'] = path2
            if hasattr(self, 'fig3'):
                path3 = os.path.abspath(os.path.join(output_dir, f"plot_evolucion_{timestamp}.png"))
                self.fig3.savefig(path3, bbox_inches='tight', dpi=100, transparent=True)
                rutas['evolucion'] = path3
            return rutas
        except Exception as e:
            print(f"Error al guardar las gráficas: {e}")
            return {}

    @pyqtSlot()
    def on_send_email(self):
        if not self.current_selected_date_str:
            QMessageBox.warning(self, "Error", "Por favor, seleccione un reporte de la lista primero.")
            return
        date_part = self.current_selected_date_str[0:8]
        time_part = self.current_selected_date_str[8:]
        pdf_name = f"Reporte_{date_part}_{time_part}.pdf"
        pdf_path = os.path.join(self.scan_dir, pdf_name)

        if not os.path.exists(pdf_path):
            QMessageBox.warning(self, "PDF no encontrado", f"No se encontró {pdf_name}.\nGenere el PDF primero.")
            return
        recipient_email, ok = QInputDialog.getText(self, "Enviar Email", "Email del destinatario:")
        if ok and recipient_email:
            try:
                QApplication.setOverrideCursor(Qt.WaitCursor)
                send_report_email(recipient_email, pdf_path, f"{date_part} {time_part}")
                QApplication.restoreOverrideCursor()
                QMessageBox.information(self, "Éxito", f"Reporte enviado a {recipient_email}")
            except Exception as e:
                QApplication.restoreOverrideCursor()
                QMessageBox.critical(self, "Error", f"Error envío: {e}")

    @pyqtSlot()
    def on_convert_pdf(self):
        if not self.current_selected_date_str:
            QMessageBox.warning(self, "Error", "Seleccione reporte primero.")
            return
        rutas_imagenes = self.guardar_plots_para_pdf()
        if not rutas_imagenes:
            QMessageBox.warning(self, "Error", "Error generando gráficas para PDF.")
            return

        img_clases = rutas_imagenes.get('clases')
        img_salud = rutas_imagenes.get('salud')
        img_evolucion = rutas_imagenes.get('evolucion')

        date_part = self.current_selected_date_str[0:8]
        time_part = self.current_selected_date_str[8:]
        suffix = f"_{date_part}_{time_part}"

        path_mapa_salud = os.path.join(self.scan_dir, f"diagnostico_mapa{suffix}.png")
        path_mapa_enf = os.path.join(self.scan_dir, f"diagnostico_enfermedades{suffix}.png")
        pdf_name = f"Reporte{suffix}.pdf"
        pdf_path = os.path.join(self.scan_dir, pdf_name)

        if not all(os.path.exists(f) for f in [path_mapa_salud, path_mapa_enf]):
            QMessageBox.warning(self, "Faltan Archivos", "Faltan imágenes de mapas.")
            return

        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            self._generar_reporte_pdf(pdf_path, path_mapa_salud, path_mapa_enf, img_clases, img_salud, img_evolucion)
            QApplication.restoreOverrideCursor()
            QMessageBox.information(self, "PDF Generado", f"PDF generado: {pdf_name}")
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Error", f"Error generando PDF: {e}")

    def _generar_reporte_pdf(self, pdf_path, path_mapa_salud, path_mapa_enf, plot_clases, plot_enf, plot_evolucion):
        c = canvas.Canvas(pdf_path, pagesize=A4)
        width, height = A4
        margin = 0.75 * inch
        top = height - margin

        c.setFont("Helvetica-Bold", 18)
        c.drawCentredString(width / 2, top, "Reporte de Diagnóstico de Cultivo")
        top -= 0.5 * inch

        fecha_str = f"{self.current_selected_date_str[6:8]}/{self.current_selected_date_str[4:6]}/{self.current_selected_date_str[0:4]}"
        c.setFont("Helvetica", 12)
        c.drawCentredString(width / 2, top, f"Fecha: {fecha_str}")
        top -= 0.3 * inch
        c.line(margin, top, width - margin, top)
        top -= 0.4 * inch

        c.setFont("Helvetica-Bold", 14);
        c.drawString(margin, top, "Resumen")
        top -= 0.25 * inch
        styles = getSampleStyleSheet()
        p = Paragraph("Se realizó el monitoreo en cultivo de maíz obteniendo los siguientes resultados.",
                      styles['BodyText'])
        p.wrapOn(c, width - 2 * margin, 0.2 * inch)
        p.drawOn(c, margin, top - p.height)
        top -= (p.height + 0.4 * inch)

        c.setFont("Helvetica-Bold", 14);
        c.drawString(margin, top, "Estadísticas")
        top -= 0.2 * inch

        img_pie = ImageReader(plot_clases)
        img_bar = ImageReader(plot_enf)
        img_width = (width - 2 * margin - 0.25 * inch) / 2
        img_h = img_width * (img_pie.getSize()[1] / img_pie.getSize()[0])

        c.drawImage(img_pie, margin, top - img_h, width=img_width, height=img_h)
        c.drawImage(img_bar, margin + img_width + 0.25 * inch, top - img_h, width=img_width, height=img_h)
        top -= (img_h + 0.4 * inch)

        c.setFont("Helvetica-Bold", 14);
        c.drawString(margin, top, "Mapas")
        top -= 0.2 * inch

        img_m1 = ImageReader(path_mapa_salud)
        img_m2 = ImageReader(path_mapa_enf)
        img_m_h = img_width * (img_m1.getSize()[1] / img_m1.getSize()[0])

        c.drawImage(img_m1, margin, top - img_m_h, width=img_width, height=img_m_h)
        c.drawImage(img_m2, margin + img_width + 0.25 * inch, top - img_m_h, width=img_width, height=img_m_h)

        c.save()

    @pyqtSlot()
    def on_open_gallery(self):
        path = os.path.abspath(self.scan_dir)
        try:
            os.system(f'xdg-open "{path}"')
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Error abriendo galería: {e}")


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

        self.background_pixmap = QPixmap("./icons/field_map.png")
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
                if self.parent.current_step == 0:
                    self.clear_selection()
                    self.selected_point = pos
                    self.draw_point(pos)
                elif self.parent.current_step == 1:
                    self.perimeter_points.append(pos)
                    self.draw_point(pos, color=Qt.red)
                    if len(self.perimeter_points) >= 3:
                        self.draw_polygon()

    def draw_point(self, pos, color=Qt.green):
        ellipse = self.scene.addEllipse(pos.x() - 5, pos.y() - 5, 10, 10, QPen(color), QBrush(color))
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
        for item in self.scene.items():
            if isinstance(item, QGraphicsEllipseItem):
                self.scene.removeItem(item)

    def reset(self):
        self.clear_selection()
        self.scene.clear()
        self.bg_item = QGraphicsPixmapItem(self.background_pixmap)
        self.scene.addItem(self.bg_item)