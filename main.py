import sys

from PyQt5 import QtCore, QtGui
from PyQt5.QtWidgets import QMainWindow, QApplication, QPushButton, QApplication
from PyQt5.QtCore import pyqtSlot, QFile, QTextStream
from matplotlib.figure import Figure
import numpy as np
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget,
    QLabel, QSizePolicy
)
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from design import Ui_window


# --- PÁGINA TABLERO ---
class TableroPage(QWidget):
    def __init__(self, parent=None):
        super(TableroPage, self).__init__(parent)
        self.initUI()

    def initUI(self):
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

        # Gráfico circular y análisis
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
class DiagnosticarPage(QWidget):
    def __init__(self, parent=None):
        super(DiagnosticarPage, self).__init__(parent)
        self.current_step = 1  # 1: seleccionar punto, 2: seleccionar perímetro, 3: resultados
        self.initUI()

    def initUI(self):
        layout = QVBoxLayout(self)

        # Título
        self.title_label = QLabel("Nuevo diagnóstico")
        self.title_label.setStyleSheet("font-size: 20px; font-weight: bold;")
        layout.addWidget(self.title_label)

        # Subtítulo dinámico
        self.subtitle_label = QLabel("Selecciona el punto de despegue y aterrizaje (Base)")
        self.subtitle_label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(self.subtitle_label)

        # Contenedor principal
        main_container = QHBoxLayout()

        # Mapa (simulado con imagen o widget)
        self.map_container = QVBoxLayout()
        self.map_label = QLabel()
        self.map_label.setPixmap(QtGui.QPixmap("./icons/map-placeholder.jpg").scaled(600, 400, QtCore.Qt.KeepAspectRatio))
        self.map_label.setAlignment(QtCore.Qt.AlignCenter)
        self.map_container.addWidget(self.map_label)

        # Botones de zoom
        zoom_layout = QHBoxLayout()
        zoom_in_btn = QPushButton("+")
        zoom_out_btn = QPushButton("-")
        zoom_layout.addWidget(zoom_in_btn)
        zoom_layout.addWidget(zoom_out_btn)
        self.map_container.addLayout(zoom_layout)

        main_container.addLayout(self.map_container)

        # Panel derecho
        right_panel = QVBoxLayout()
        right_panel.setAlignment(QtCore.Qt.AlignTop)

        # Botones de acción
        action_layout = QHBoxLayout()
        undo_btn = QPushButton("Deshacer")
        redo_btn = QPushButton("Rehacer")
        action_layout.addWidget(undo_btn)
        action_layout.addWidget(redo_btn)
        right_panel.addLayout(action_layout)

        # Estado del perímetro
        self.perimeter_status = QLabel("✅ Perímetro cerrado\nBase\nSeleccionada (25.907799, -108.797125)")
        self.perimeter_status.setStyleSheet("background-color: #f0f0f0; padding: 10px; border-radius: 5px;")
        right_panel.addWidget(self.perimeter_status)

        # Botón Siguiente
        next_btn = QPushButton("Siguiente")
        next_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; padding: 10px;")
        next_btn.clicked.connect(self.go_to_next_step)
        right_panel.addWidget(next_btn)

        main_container.addLayout(right_panel)

        layout.addLayout(main_container)

        # Barra inferior de estado del dron
        drone_status = QHBoxLayout()
        drone_status.addWidget(QLabel("📡 Dron(Conectado)"))
        drone_status.addStretch()
        layout.addLayout(drone_status)

    def go_to_next_step(self):
        if self.current_step == 1:
            self.current_step = 2
            self.title_label.setText("Nuevo diagnóstico")
            self.subtitle_label.setText("Selecciona el perímetro de recorrido")
            self.perimeter_status.setText("✅ Perímetro cerrado")
            self.map_label.setPixmap(QtGui.QPixmap("./icons/map-perimeter.jpg").scaled(600, 400, QtCore.Qt.KeepAspectRatio))
        elif self.current_step == 2:
            self.current_step = 3
            self.show_final_results()

    def show_final_results(self):
        # Limpiar layout actual
        for i in reversed(range(self.layout().count())):
            self.layout().itemAt(i).widget().setParent(None)

        # Título final
        title = QLabel("¡Diagnóstico Finalizado!")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: green;")
        self.layout().addWidget(title)

        # Fecha y hora
        date_label = QLabel("Resultados de diagnóstico - 01/Mayo/2025 - 11:18 AM")
        date_label.setAlignment(QtCore.Qt.AlignCenter)
        self.layout().addWidget(date_label)

        # Contenedor de resultados
        results_layout = QHBoxLayout()

        # Imagen con puntos
        img_layout = QVBoxLayout()
        img_label = QLabel()
        img_label.setPixmap(QtGui.QPixmap("./icons/map-results.jpg").scaled(400, 300, QtCore.Qt.KeepAspectRatio))
        img_layout.addWidget(img_label)
        results_layout.addLayout(img_layout)

        # Gráfico circular de clasificación
        pie_layout = QVBoxLayout()
        pie_title = QLabel("Clasificación del total de fotos tomadas")
        pie_title.setStyleSheet("font-weight: bold;")
        pie_layout.addWidget(pie_title)

        fig_pie = Figure(figsize=(4, 4), dpi=100)
        ax_pie = fig_pie.add_subplot(111)
        pie_data = [70, 13, 9, 8]  # Saludables, Leves, Considerables, Altos
        pie_labels = ['Saludables 70%', 'Con leves rasgos 13%', 'Con rasgos considerables 9%', 'Con rasgos altos 8%']
        colors = ['#00FF00', '#FFFF00', '#FFA500', '#FF4500']
        ax_pie.pie(pie_data, labels=pie_labels, autopct='%1.1f%%', colors=colors, startangle=90)
        ax_pie.axis('equal')

        canvas_pie = FigureCanvas(fig_pie)
        pie_layout.addWidget(canvas_pie)
        results_layout.addLayout(pie_layout)

        # Botones de acción
        action_layout = QVBoxLayout()
        save_btn = QPushButton("Guardar")
        print_btn = QPushButton("Imprimir")
        photos_btn = QPushButton("Ver fotos")
        end_btn = QPushButton("Terminar")

        for btn in [save_btn, print_btn, photos_btn, end_btn]:
            btn.setStyleSheet("background-color: #333; color: white; padding: 8px;")
            action_layout.addWidget(btn)

        results_layout.addLayout(action_layout)

        self.layout().addLayout(results_layout)

        # Barra inferior de estado
        status_bar = QHBoxLayout()
        status_bar.addWidget(QLabel("🚁 Sensores: Buen estado"))
        status_bar.addWidget(QLabel("🔋 Batería: 65%"))
        status_bar.addWidget(QLabel("⏱ Tiempo de análisis: 1 h 15 min"))
        status_bar.addWidget(QLabel("✈️ Tiempo de vuelo: 27 min"))
        self.layout().addLayout(status_bar)


# --- PÁGINA ESTADÍSTICOS (propuesta) ---
class EstadisticosPage(QWidget):
    def __init__(self, parent=None):
        super(EstadisticosPage, self).__init__(parent)
        self.initUI()

    def initUI(self):
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


class MainWindow(QMainWindow):
    def __init__(self):
        super(MainWindow, self).__init__()

        self.ui = Ui_window()
        self.ui.setupUi(self)

        self.ui.short_menu_bar.hide()
        self.ui.stackedWidget.setCurrentIndex(0)
        self.ui.btn_tablero_2.setChecked(True)

        # Inicializar páginas
        self.tablero_page = TableroPage()
        self.diagnosticar_page = DiagnosticarPage()
        self.estadisticos_page = EstadisticosPage()

        # Reemplazar páginas vacías
        self.ui.stackedWidget.widget(0).deleteLater()
        self.ui.stackedWidget.widget(1).deleteLater()
        self.ui.stackedWidget.widget(2).deleteLater()

        self.ui.stackedWidget.addWidget(self.tablero_page)
        self.ui.stackedWidget.addWidget(self.diagnosticar_page)
        self.ui.stackedWidget.addWidget(self.estadisticos_page)

        # Conectar señales de botones
        self.ui.btn_tablero.toggled.connect(self.on_btn_1_tablero_toggled)
        self.ui.btn_diagnosticar.toggled.connect(self.on_btn_1_diagnosticar_toggled)
        self.ui.btn_estadisticos.toggled.connect(self.on_btn_1_estadisticos_toggled)

        self.ui.btn_tablero_2.toggled.connect(self.on_btn_2_tablero_toggled)
        self.ui.btn_diagnosticar_2.toggled.connect(self.on_btn_2_diagnosticar_toggled)
        self.ui.btn_estadisticos_2.toggled.connect(self.on_btn_2_estadisticos_toggled)

    # Funciones para cambiar de páginas
    def on_btn_1_tablero_toggled(self):
        if self.ui.btn_tablero.isChecked():
            self.ui.stackedWidget.setCurrentIndex(0)

    def on_btn_2_tablero_toggled(self):
        if self.ui.btn_tablero_2.isChecked():
            self.ui.stackedWidget.setCurrentIndex(0)

    def on_btn_1_diagnosticar_toggled(self):
        if self.ui.btn_diagnosticar.isChecked():
            self.ui.stackedWidget.setCurrentIndex(1)

    def on_btn_2_diagnosticar_toggled(self):
        if self.ui.btn_diagnosticar_2.isChecked():
            self.ui.stackedWidget.setCurrentIndex(1)

    def on_btn_1_estadisticos_toggled(self):
        if self.ui.btn_estadisticos.isChecked():
            self.ui.stackedWidget.setCurrentIndex(2)

    def on_btn_2_estadisticos_toggled(self):
        if self.ui.btn_estadisticos_2.isChecked():
            self.ui.stackedWidget.setCurrentIndex(2)



if __name__ == "__main__":
    app = QApplication(sys.argv)

    # Cargando hoja de estilos e íconos
    with open("./style.qss", "r") as style_file:
        style_str = style_file.read()
    app.setStyleSheet(style_str)

    # ejecutando ventana
    window = MainWindow()
    window.show()

    # Cierre
    sys.exit(app.exec())




