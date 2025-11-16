import json
from PyQt5 import QtCore, QtGui
from PyQt5.QtCore import Qt, pyqtSlot, QPointF, QRectF, QTimer, QFile, QTextStream, QUrl, QObject, pyqtSignal, QIODevice
from PyQt5.QtGui import QPixmap, QPainter, QColor, QPen, QPolygonF, QIcon, QBrush, QFont
import numpy as np
import sys
from PyQt5.QtWidgets import (QMainWindow, QApplication, QPushButton, QVBoxLayout, QStackedWidget, QSizePolicy,
                             QGraphicsScene, QGraphicsEllipseItem, QHBoxLayout, QLabel, QWidget, QFrame, QListWidget,
                             QScrollArea, QMessageBox, QGraphicsView, QGraphicsPixmapItem, QUndoStack, QUndoCommand)

from PyQt5.QtWebChannel import QWebChannel
from PyQt5.QtWebEngineWidgets import QWebEngineView
from matplotlib.figure import Figure
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
import io
import datetime
import folium
from sklearn.externals.array_api_compat.torch import empty

from geopy.distance import geodesic
from shapely.geometry import Polygon
from pyproj import Geod

from design import Ui_window

from predictor import *


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
        self.class_counts = {}
        self.list_results_per_class = {}
        self.list_leaf_state = {}

        # --- CAMBIOS AQUÍ ---
        # Inicializa con datos placeholder para que se muestre algo
        self.dates = ['Esperando...']
        self.data = [[100.0]]  # 100% de una categoría
        self.data_is_placeholder = True  # Un flag para saber que son datos de inicio
        # --- FIN CAMBIOS ---

        self.label_colores_prob_enfermedad = ['#006A35', '#34A853', '#FBBC04', '#F47C34', '#EA4335']  # De verde a rojo
        self.label_probabilidad_enfermedad = [
            'Saludables',  # (85 < saludables <= 100%)
            'Leves rasgos',  # (65 < saludables <= 85)
            'Rasgos considerables',  # (30% < saludables <= 65)
            'Rasgos altos',  # (15% < saludables <= 30)
            'Enfermas'  # (0 <= saludables <= 15%)
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
            lbl.setStyleSheet(f"background-color: {color};"
                              f"padding: 5px;"
                              f"border-radius: 5px;"
                              f"font-weight: bold;"
                              f"font-size: 14px")
            if i in [0, 1, 4]:  # Cambiar en blanco según el indice de los labels de colores
                lbl.setStyleSheet(f"color: #F5F5F5;"
                                  f"background-color: {color};"
                                  f"padding: 5px;"
                                  f"border-radius: 5px;"
                                  f"font-weight: bold;"
                                  f"font-size: 14px")

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
        print("Recibidos en", self.__class__.__name__)  # Esto ahora sí se debe imprimir

        self.list_results_per_class = list_results_per_class
        self.list_leaf_state = list_leaf_state

        # --- LÓGICA DE GRÁFICO DE PASTEL (Tu código estaba bien) ---
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
            # Multiplicar por 100 para obtener el porcentaje
            counts_percent = [(c / total_count) * 100.0 for c in counts_raw]

        new_date = datetime.datetime.now().strftime("%d/%m/%y-%H:%M:%S")

        # 2. Reemplazar datos placeholder o añadir nuevos
        if self.data_is_placeholder:
            self.data = [counts_percent.copy()]  # Almacena porcentajes
            self.dates = [new_date]
            self.data_is_placeholder = False  # Ya no somos placeholder
        else:
            self.data.append(counts_percent.copy())
            self.dates.append(new_date)

        # 3. Limpiar y redibujar el gráfico de barras
        self.ax_bar.cla()  # Limpiar eje

        bottom = [0] * len(self.dates)

        # Iterar 5 veces (por las 5 categorías)
        for i in range(len(self.label_probabilidad_enfermedad)):
            # Obtener el i-ésimo valor de cada fecha (ej. todos los % "Saludables")
            values = [d[i] for d in self.data]

            self.ax_bar.bar(self.dates, values, bottom=bottom,
                            color=self.label_colores_prob_enfermedad[i],
                            label=self.label_probabilidad_enfermedad[i])
            # Actualizar la base para la siguiente categoría
            bottom = [b + v for b, v in zip(bottom, values)]

        # 4. ¡IMPORTANTE! Re-aplicar formato después de .cla()
        self.ax_bar.tick_params(axis='x', rotation=20, labelsize=8)
        self.ax_bar.set_ylabel('% del Total de diagnósticos')
        self.ax_bar.set_xlabel('Fecha de diagnóstico')
        self.ax_bar.legend(loc='upper right', fontsize=8)
        self.ax_bar.set_ylim(0, 100)  # El límite de 100 ahora es correcto

        # 5. Redibujar el canvas
        self.canvas_bar.draw()


# --- PÁGINA DIAGNOSTICAR ---
class page_diagnosticar(QWidget):
    # Emisor de señales para comunicar en las otras clases los resultados del diagnostico de IA
    diagnostico_completo = pyqtSignal(dict, dict, dict)

    def __init__(self):
        super().__init__()
        self.current_step = 0
        self.current_results = []
        self.current_state_filename = {}
        self.conectado = False

        # Datos del plot
        self.counts = []
        self.label_colores_prob_enfermedad = ['#006A35', '#34A853', '#FBBC04', '#F47C34', '#EA4335']  # De verde a rojo
        self.label_probabilidad_enfermedad = [
            'Saludables',  # (85 < saludables <= 100%)
            'Leves rasgos',  # (65 < saludables <= 85)
            'Rasgos considerables',  # (30% < saludables <= 65)
            'Rasgos altos',  # (15% < saludables <= 30)
            'Enfermas'  # (0 <= saludables <= 15%)
        ]

        # Listas para guardar las coordenadas de cada paso
        self.perimeter_points = []  # Para los 4 puntos de la página 1
        self.start_point = None  # Para el punto único de la página 2
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

        self.page5 = self.create_page5()
        self.stacked_widget.addWidget(self.page5)

        self.update_page()

    # --- PÁGINA 1: MODIFICADA ---
    def create_page0(self):
        page = QWidget()
        # Guarda el layout para poder añadirle cosas después
        self.page1_layout = QVBoxLayout(page)

        title = QLabel("Paso previo del Punto de Despegue")
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        self.page1_layout.addWidget(title)

        # Crea el mensaje de "bloqueado"
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
        # self.bridge1.mapClicked.connect(self.handle_start_point_map_click)
        self.web_view1.setHtml(self.get_map_html(), QUrl("qrc:///"))

        # Botones
        # btn_deshacer = QPushButton("Limpiar Selección")
        # btn_deshacer.setStyleSheet("background-color: #f44336; color: white;")
        # btn_deshacer.clicked.connect(self.clear_start_point_marker)
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
        self.status_label2.setStyleSheet("color: green; font-size: 19px")
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
        # Guarda el layout para poder añadirle cosas después
        layout = QVBoxLayout(page)

        title = QLabel("Paso 3: Realizando diagnostico!")
        title.setStyleSheet("font-size: 26px; font-weight: bold;")
        layout.addWidget(title)

        # 1. Crea el mensaje de "bloqueado"
        self.locked_label = QLabel("El UAV se encuentra en movimiento.")
        self.locked_label.setStyleSheet(
            "color: Black; font-size: 20px; font-weight: bold; qproperty-alignment: 'AlignCenter';")
        n = 0
        text = QLabel(str(n) + "% monitoreado")
        text.setStyleSheet("font-size: 16px;")
        layout.addWidget(text)
        layout.addWidget(self.locked_label)

        btn_abortar = QPushButton("Abortar operación")
        btn_abortar.setStyleSheet("background-color: #f44336; color: white;")
        btn_abortar.clicked.connect(self.abort)

        btn_siguiente = QPushButton("Siguiente")
        btn_siguiente.setStyleSheet("background-color: #4CAF50; color: white;")
        btn_siguiente.clicked.connect(self.go_to_step4)

        btn_layout = QHBoxLayout()
        btn_layout.addWidget(btn_abortar)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_siguiente)
        layout.addLayout(btn_layout)

        return page

    def create_page4(self):
        page = QWidget()
        # Guarda el layout para poder añadirle cosas después
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
        self.btn_ejecutar.clicked.connect(self.start_prediction)
        self.btn_siguiente = QPushButton("Siguiente")
        self.btn_siguiente.setStyleSheet("background-color: #4CAF50; color: white;")
        self.btn_siguiente.clicked.connect(self.go_to_step5)
        self.btn_siguiente.hide()  # Oculto hasta que se ejecute el modelo

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
        layout.setContentsMargins(15, 15, 15, 15)  # Añade un poco de espacio
        layout.setSpacing(10)

        # --- Títulos ---
        title = QLabel("¡Diagnóstico Finalizado!")
        title.setStyleSheet("font-size: 24px; font-weight: bold; color: green;")
        layout.addWidget(title)

        fecha = datetime.datetime.today().strftime("%d/%m/%y-%H:%M")
        subtitle = QLabel("Resultados de diagnóstico - " + fecha + " hrs.")
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

        # --- Gráfico de pastel ---
        # Creamos etiquetas personalizadas como en la imagen
        labels_data = [f'{l}\n{s}' for l, s in zip(self.label_probabilidad_enfermedad, self.counts)]
        fig = Figure(figsize=(5, 4), dpi=100)
        fig.patch.set_alpha(0.0)  # Fondo transparente

        self.ax = fig.add_subplot(111)
        self.ax.set_title('Clasificación del total de fotos tomadas', fontsize=12)

        self.ax.pie(self.counts, labels=labels_data, colors=self.label_colores_prob_enfermedad, startangle=90,
                    textprops={'fontsize': 9})
        self.ax.axis('equal')  # Asegura que el gráfico sea un círculo

        self.canvas_pie_5 = FigureCanvas(fig)
        self.canvas_pie_5.setStyleSheet("background-color: transparent;")
        right_panel_layout.addWidget(self.canvas_pie_5)

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
        # btn_guardar.clicked.connect(lambda: QMessageBox.information(self, "Guardar", "Guardado exitoso"))
        btn_guardar.clicked.connect(self.guardar_diagnostico)
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
        self.label_ha = QLabel("Área de monitoreo: 1 ha.")

        # (Aquí puedes añadir iconos si los tienes)
        status_bar_layout.addWidget(QLabel("Sensores: Buen estado"))
        status_bar_layout.addWidget(QLabel("Batería: 41%"))
        status_bar_layout.addStretch()  # Espaciador
        status_bar_layout.addWidget(self.label_ha)
        status_bar_layout.addWidget(QLabel("Tiempo de vuelo: 7 min"))
        status_bar_layout.addWidget(QLabel("Tiempo de análisis: 0h 39 min"))

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
                var map = L.map('map').setView([20.432939, -99.598862], 18);

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
                 * @param {string} popupText - El texto a mostrar en el popup.
                 */

                function addLocationMarker(lat, lng, color) {
                    L.circleMarker([lat, lng], {
                        radius: 2,
                        color: color,         // Color del borde
                        fillColor: color,     // Color de relleno
                        fillOpacity: 0.8
                    })
                    .bindPopup("Ubicación actual")
                    .addTo(markerLayer);
                }

                function addMarker(lat, lng, color) {
                    L.circleMarker([lat, lng], {
                        radius: 2,
                        color: color,         // Color del borde
                        fillColor: color,     // Color de relleno
                        fillOpacity: 0.8
                    }).addTo(markerLayer);
                }

                function addStateMark(lat, lng, color, clase) {
                    var text = `${clase}<br>Lat: ${lat.toFixed(5)}<br>Lng: ${lng.toFixed(5)}`;
                    L.circleMarker([lat, lng], {
                        radius: 30,
                        color: color,         // Color del borde
                        fillColor: color,     // Color de relleno
                        fillOpacity: 0.6
                    })
                    .bindPopup(text)
                    .addTo(markerLayer);
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

                function drawLastPolygon(points_json) {
                    polygonLayer.clearLayers(); // Limpia polígonos anteriores
                    var points = JSON.parse(points_json);
                    L.polygon(points, {
                        color: '#5D6D7E',       // Color del borde
                        weight: 2,
                        fillColor: '#D6DBDF', // Color de relleno (rojo)
                        fillOpacity: 0.2    // Relleno rojo transparente
                    }).addTo(polygonLayer);
                }


                /**
                 * Limpia ambas capas, marcadores y polígonos.
                 */
                function clearMarkers() {
                    markerLayer.clearLayers();
                    polygonLayer.clearLayers();
                }
            </script>
        </body>
        </html>
        """

    @pyqtSlot(bool)
    def set_estado_conexion(self, conectado):
        """
        Este es el SLOT que recibe la señal desde MainWindow.
        """
        self.conectado = conectado
        self.go_to_step1()

    # --- MANEJADORES DE CLIC (SEPARADOS) ---
    @pyqtSlot(float, float)
    def handle_start_point_map_click(self, lat, lng):
        if self.start_point is not None:
            item_text = f"Punto de inicio: ({lat:.5f}, {lng:.5f})"
            self.coord_list_widget1.addItem(item_text)
            self.web_view1.page().runJavaScript(f"addLocationMarker({lat}, {lng}, 'lightgreen');")
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

    def distance(self, punto1, punto2):
        """
        Calcula la distancia geodésica entre dos puntos (lat, lon).
        """
        if not punto1 or not punto2:
            return 0.0

        distancia = geodesic(punto1, punto2).meters
        return distancia

    def calcular_hectarea(self):
        """
        Calcula el área del polígono definido en self.perimeter_points.
        """
        if len(self.perimeter_points) < 3:
            return 0.0

        # 1. Definir la forma de la Tierra (elipsoide WGS84)
        geod = Geod(ellps='WGS84')
        #    El primer punto se añade al final para cerrar el polígono.
        puntos_poligono = self.perimeter_points + [self.perimeter_points[0]]
        # Shapely espera (lon, lat) para el constructor Polygon
        puntos_lon_lat = [(lon, lat) for lat, lon in puntos_poligono]
        polygon = Polygon(puntos_lon_lat)
        # Calcular el área y el perímetro
        area, perimetro = geod.geometry_area_perimeter(polygon)
        hectarea = area * 0.01
        # Devolvemos el área (es un valor positivo)
        return abs(hectarea)

    # --- MÉTODOS DE LIMPIEZA (SEPARADOS) ---
    def clear_start_point_marker(self):
        self.start_point = (20.432939, -99.598862)  ######### pendiente
        self.coord_list_widget1.clear()
        self.web_view1.page().runJavaScript("clearMarkers();")
        self.status_label1 = QLabel("Ubicación actual del UAV.")
        self.status_label1.setStyleSheet("color: green; font-size: 19px;")

    def up_to_date_map1(self):
        # Borrando puntos del mapa y actualizando ubicación actual
        self.web_view1.page().runJavaScript("clearMarkers();")
        self.web_view1.page().runJavaScript(
            f"addLocationMarker({self.start_point[0]}, {self.start_point[1]}, 'lightgreen');")

    def up_to_date_map2(self):
        # Borrando puntos del mapa y actualizando ubicación actual
        # dibujando el punto de despegue y aterrizaje
        self.web_view2.page().runJavaScript("clearMarkers();")
        self.web_view2.page().runJavaScript(
            f"addLocationMarker({self.start_point[0]}, {self.start_point[1]}, 'lightgreen');")
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
        self.web_view2.page().runJavaScript(
            f"addLocationMarker({self.start_point[0]}, {self.start_point[1]}, 'lightgreen');")
        self.status_label2.setText("Haz clic en el mapa para seleccionar los 4 puntos del perímetro.")

    def go_to_step1(self):
        if self.conectado:
            self.current_step = 1
            self.coordenadas_iniciales = (20.432939, -99.598862)  ######### pendiente
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
            if self.start_point is not None:
                self.web_view2.page().runJavaScript(f"addLocationMarker({lat}, {lng}, 'lightgreen');")
            self.update_page()
        else:
            QMessageBox.warning(self, "Error", "Debes seleccionar un punto de despegue.")

    def go_to_step3(self):
        if len(self.perimeter_points) != 4:
            QMessageBox.warning(self, "Error", "Debes seleccionar 4 puntos.")
        elif self.calcular_hectarea() > 1.00:
            QMessageBox.warning(self, "Error", f'{self.calcular_hectarea():.3f}'+" ha seleccionados.\nSolo se permite 1.00 ha como máximo")
        else:
            self.current_step = 3
            self.update_page()

    def go_to_step4(self):
        self.current_step = 4
        self.label_ha.setText("Área monitoreado:" + f'{self.calcular_hectarea():.3f}' + "ha.")
        self.update_page()

    def go_to_step5(self):
        if (len(self.perimeter_points) == 4) and (self.current_results is not None):
            self.current_step = 5
            # Limpiar centrar y marcar mapa
            self.web_view4.page().runJavaScript("clearMarkers();")
            self.web_view4.page().runJavaScript(f"map.setView([{self.start_point[0]}, {self.start_point[1]}], {18});")
            self.web_view4.page().runJavaScript(
                f"addLocationMarker({self.start_point[0]}, {self.start_point[1]}, 'lightgreen');")

            # Marca del área de monitoreo
            for p in self.perimeter_points:
                self.web_view4.page().runJavaScript(f"addMarker({p[0]}, {p[1]}, '#5D6D7E');")
            else:
                points_json = json.dumps(self.perimeter_points)
                self.web_view4.page().runJavaScript(f"drawLastPolygon('{points_json}');")

            # Dibujando mapa de colores
            for i, estado in enumerate(self.current_state_filename.keys()):
                color = self.label_colores_prob_enfermedad[i]
                for coord in self.current_state_filename[estado]:
                    print(i, estado, coord)
                    self.web_view4.page().runJavaScript(f"addStateMark({coord[0]}, {coord[1]}, '{color}', '{estado}');")

            # Actualizando plots
            if len(self.counts) != 0:
                # 1. Limpia el gráfico anterior
                self.ax.cla()

                # 2. Prepara las nuevas etiquetas y colores
                # (Usando self.labels y self.counts que ya fueron actualizados)
                labels_data = [f'{l}\n{s}' for l, s in zip(self.label_probabilidad_enfermedad, self.counts)]
                print(self.counts)
                print(labels_data)

                # 3. Vuelve a dibujar el gráfico de pastel en el MISMO axis (self.ax)
                self.ax.pie(self.counts, labels=labels_data, colors=self.label_colores_prob_enfermedad, startangle=90,
                            textprops={'fontsize': 9})
                self.ax.axis('equal')
                self.ax.set_title('Clasificación del total de fotos tomadas', fontsize=12)
                self.canvas_pie_5.draw()

            self.update_page()

        else:
            QMessageBox.warning(self, "Error", "Debes seleccionar exactamente 4 puntos para el perímetro.")

    def guardar_diagnostico(self):
        """
            Toma una captura del web_view4 y la guarda como un archivo PNG.
        """
        # Crear un nombre de archivo único
        fecha_hora = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"diagnostico_mapa_{fecha_hora}.png"

        try:
            # Usar .grab() para tomar una captura del widget QWebEngineView
            pixmap = self.web_view4.grab()
            # Guardar el QPixmap en un archivo
            success = pixmap.save("./diagnosticos_guardados/"+filename, "PNG")

            if success: QMessageBox.information(self,"Guardado Exitoso", f"El mapa se ha guardado como:\n{filename}")
            else: QMessageBox.warning(self, "Error al Guardar", "No se pudo guardar la imagen del mapa.")

        except Exception as e:
            QMessageBox.warning(self, "Error", f"Ocurrió un error al capturar el mapa: {e}")


    def come_back_to_step1(self):
        self.clear_perimeter_markers()
        # self.clear_start_point_marker()
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

    def start_prediction(self):
        self.btn_ejecutar.setText("Ejecutando")
        self.btn_ejecutar.setEnabled(False)
        self.text.setText("Se encuentran procesando las fotos recopiladas, espere por favor")
        QApplication.processEvents()

        # Etiquetas para las clasificaciones
        CLASES_DEL_MODELO = [
            'common_rust',
            'gray_leaf_spot',
            'healthy',
            'northern_leaf_blight',
            'northern_leaf_spot'
        ]

        # Hiperparámetros
        NUM_CLASES = 5
        PATH_MODELO = "./models/densenet_201_fold4.pth"
        PATH_FOTOS = "./fotos_path_pruebas_2"

        # Carga y ejecución del modelo
        classifier = ImageClassifier(model_path=PATH_MODELO, num_classes=NUM_CLASES, class_names=CLASES_DEL_MODELO)
        self.current_results = classifier.predict_folder(PATH_FOTOS)

        # Cuando el modelo devuelva resultados
        if self.current_results:
            print("\n--- Resultados de la Clasificación ---")
            for filename, info in self.current_results.items():
                print(
                    f"{filename} -> Clase: {info['clase']:<20} (Confianza: {info['confianza']}%). C_healthy: {info['confianza healthy']}$")

            # Conteo de resultados
            class_counts = {class_name: 0 for class_name in CLASES_DEL_MODELO}
            class_file_lists = {class_name: [] for class_name in CLASES_DEL_MODELO}
            state_file_lists = {class_name: [] for class_name in self.label_probabilidad_enfermedad}
            class_leaf_state = {class_name: 0 for class_name in self.label_probabilidad_enfermedad}

            # Iterar sobre los resultados y rellenar los diccionarios
            for filename, info in self.current_results.items():
                predicted_class = info['clase']

                # Verificamos si la clase predicha es una de las que esperamos
                if predicted_class in class_counts:
                    class_counts[predicted_class] += 1
                    class_file_lists[predicted_class].append(filename)
                    coord_raw = os.path.splitext(filename)[0].split(", ")
                    coord = [float(c) for c in coord_raw]

                # Registramos el estado de cada resultado obtenido
                confianza = float(info['confianza healthy'])
                if 0.0 <= confianza < 15.0:
                    class_leaf_state["Enfermas"] += 1
                    state_file_lists["Enfermas"].append(coord)
                elif 15.0 <= confianza < 30.0:
                    class_leaf_state["Rasgos altos"] += 1
                    state_file_lists["Rasgos altos"].append(coord)
                elif 30.0 <= confianza < 65.0:
                    class_leaf_state["Rasgos considerables"] += 1
                    state_file_lists["Rasgos considerables"].append(coord)
                elif 65.0 <= confianza < 85.0:
                    class_leaf_state["Leves rasgos"] += 1
                    state_file_lists["Leves rasgos"].append(coord)
                elif 85.0 <= confianza <= 100.0:
                    class_leaf_state["Saludables"] += 1
                    state_file_lists["Saludables"].append(coord)
            else:
                for i in class_leaf_state.keys():
                    print(i, class_leaf_state[i])
                else:
                    print("---")

            # .countsActualizar los resultados del modelo
            nuevos_conteos = list(class_leaf_state.values())
            if len(self.counts) == 0:
                self.counts = nuevos_conteos

            # Envio de la señal
            self.diagnostico_completo.emit(class_counts, class_file_lists, class_leaf_state)
            self.btn_ejecutar.hide()
            self.btn_siguiente.show()
            self.current_state_filename = state_file_lists
            self.text.setText("Procesamiento realizado")


# --- PÁGINA ESTADÍSTICOS (propuesta) ---
class page_Estadisticas(QWidget):
    def __init__(self, parent=None):
        super(page_Estadisticas, self).__init__(parent)
        self.class_counts = {}
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

        ##

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

        ##
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

    @pyqtSlot(dict, dict, dict)
    def set_result_plots(self, class_counts, list_results_per_class, list_leaf_state):
        """
        Este es el SLOT que recibe los datos de las clasificaciones.
        """
        self.class_counts = class_counts
        self.list_results_per_class = list_results_per_class
        print("Recibidos en", self.__class__.__name__)


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
        # Limpiar todos los puntos
        for item in self.scene.items():
            if isinstance(item, QGraphicsEllipseItem):
                self.scene.removeItem(item)

    def reset(self):
        self.clear_selection()
        self.scene.clear()
        self.bg_item = QGraphicsPixmapItem(self.background_pixmap)
        self.scene.addItem(self.bg_item)