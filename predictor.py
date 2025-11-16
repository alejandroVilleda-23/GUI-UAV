import torch
import torch.nn as nn
from matplotlib import pyplot as plt
from torchvision import models, transforms
from PIL import Image
import os


class ImageClassifier:
    """
    Clase para cargar un modelo DenseNet-201 entrenado y
    realizar predicciones de imágenes.
    """
    def __init__(self, model_path, num_classes, class_names):
        """
        Inicializa el clasificador.

        Args:
            model_path (str): Ruta al archivo .pth del modelo entrenado.
            num_classes (int): Número de clases (ej. 5).
            class_names (list): Lista de strings con los nombres de las clases,
                                en el orden correcto.
        """
        if len(class_names) != num_classes:
            raise ValueError("La longitud de 'class_names' debe ser igual a 'num_classes'")

        self.device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        self.class_names = class_names

        # --- 1. Definir las transformaciones de 'test' ---
        # ¡DEBEN ser idénticas a las usadas en tu script de entrenamiento!
        # (DenseNet también usa 224x224, así que esto debería estar bien)
        self.transforms = transforms.Compose([
            transforms.Resize(256),
            transforms.CenterCrop(224),
            transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
        ])

        # --- 2. Cargar la arquitectura del modelo ---
        # ¡CAMBIO! Usamos densenet201 en lugar de efficientnet_b4
        self.model = models.densenet201(pretrained=False)

        # --- 3. Reemplazar la capa final ---
        # ¡CAMBIO! DenseNet accede a su capa final de forma diferente
        # Se llama 'classifier' y es una sola capa nn.Linear
        num_ftrs = self.model.classifier.in_features

        if model_path == "./models/densenet_201_fold4.pth":
            # Replicamos la estructura que el archivo .pth SÍ tiene:
            self.model.classifier = nn.Sequential(
                nn.Dropout(p=0.4, inplace=True),  # Asumimos un Dropout, ya que es común
                nn.Linear(num_ftrs, num_classes)  # Esta es la capa [1]
            )
        else:
            self.model.classifier = nn.Linear(num_ftrs, num_classes)

        # --- 4. Cargar los pesos entrenados (.pth) ---
        try:
            self.model.load_state_dict(torch.load(model_path, map_location=self.device))
            print(f"Modelo cargado exitosamente desde {model_path}")
        except Exception as e:
            print(f"Error cargando el modelo: {e}")
            print("Asegúrate de que 'num_classes' y la arquitectura coincidan.")
            raise

        # --- 5. Poner el modelo en modo de evaluación ---
        # Esto es CRUCIAL: desactiva dropout, batchnorm, etc.
        self.model.to(self.device)
        self.model.eval()

    def _preprocess_image(self, image_path):
        """Abre, pre-procesa y prepara una sola imagen."""
        try:
            image = Image.open(image_path).convert('RGB')
        except Exception as e:
            print(f"Error al abrir la imagen {image_path}: {e}")
            return None

        # Aplicar transformaciones
        image_tensor = self.transforms(image)

        # Añadir una dimensión de 'batch' (PyTorch espera BxCxHxW)
        image_tensor = image_tensor.unsqueeze(0)

        return image_tensor.to(self.device)

    def predict_image(self, image_path):
        """
        Predice la clase de una sola imagen.

        Args:
            image_path (str): Ruta al archivo de imagen.

        Returns:
            (str, float): (nombre_de_clase_predicha, confianza)
        """
        image_tensor = self._preprocess_image(image_path)
        if image_tensor is None:
            return None, 0.0

        # Realizar la inferencia
        with torch.no_grad():
            outputs = self.model(image_tensor)

            # Aplicar Softmax para obtener probabilidades
            probabilities = torch.nn.functional.softmax(outputs, dim=1)

            # Obtener la clase con mayor probabilidad
            confidence, pred_idx = torch.max(probabilities, 1)
            print(pred_idx.item(), type(pred_idx), type(int(pred_idx)))

            predicted_class = self.class_names[pred_idx.item()]

            return predicted_class, confidence.item(), probabilities.tolist()[0][2]

    def predict_folder(self, folder_path):
        """
        Clasifica todas las imágenes en una carpeta.

        Args:
            folder_path (str): Ruta a la carpeta (ej. "fotos_path").

        Returns:
            dict: Un diccionario con {nombre_archivo: {clase, confianza}}
        """
        if not os.path.isdir(folder_path):
            print(f"Error: La carpeta {folder_path} no existe.")
            return {}

        predictions = {}
        allowed_extensions = {'.jpg', '.jpeg', '.png', '.bmp', '.gif'}

        print(f"Clasificando imágenes en: {folder_path}")
        for filename in os.listdir(folder_path):
            file_path = os.path.join(folder_path, filename)

            # Asegurarse de que es un archivo y tiene una extensión de imagen
            if os.path.isfile(file_path) and \
                    os.path.splitext(filename)[1].lower() in allowed_extensions:

                try:
                    predicted_class, confidence, c_healthy = self.predict_image(file_path)

                    if predicted_class is not None:
                        predictions[filename] = {
                            'clase': predicted_class,
                            'confianza': f'{confidence * 100:.2f}',
                            'confianza healthy': f'{c_healthy * 100:.2f}'
                        }
                except Exception as e:
                    print(f"No se pudo procesar {filename}: {e}")
                    predictions[filename] = {'clase': 'Error', 'confianza': '0.00%'}

        return predictions


# -----------------------------------------------------------------
# --- EJEMPLO DE CÓMO USAR ESTA CLASE EN OTRO ARCHIVO ---
# -----------------------------------------------------------------
if __name__ == "__main__":

    # --- 1. CONFIGURACIÓN ---
    CLASES_DEL_MODELO = [
        'common_rust',
        'gray_leaf_spot',
        'healthy',
        'northern_leaf_blight',
        'northern_leaf_spot'
    ]  # <--- ¡EDITAR ESTO ES OBLIGATORIO!

    # ¡CAMBIO! Asegúrate de que esta ruta apunte a tu modelo DenseNet-201
    PATH_MODELO = "./models/densenet_201_fold4.pth"
    PATH_FOTOS = "fotos_path_pruebas_2"
    NUM_CLASES = 5

    print("--- Ejemplo de Clasificación de Carpeta (DenseNet-201) ---")

    if CLASES_DEL_MODELO[0] == 'clase_0_sana':
        print("****************************************************************")
        print("ADVERTENCIA: Estás usando los nombres de clase de ejemplo.")
        print("Edita la lista 'CLASES_DEL_MODELO' en este script para que")
        print("coincida con las clases reales de tu modelo.")
        print("****************************************************************")

    # --- 2. INICIALIZAR EL CLASIFICADOR ---
    try:
        classifier = ImageClassifier(
            model_path=PATH_MODELO,
            num_classes=NUM_CLASES,
            class_names=CLASES_DEL_MODELO
        )

        # --- 3. CLASIFICAR LA CARPETA ---
        results = classifier.predict_folder(PATH_FOTOS)

        # --- 4. MOSTRAR RESULTADOS ---
        if results:
            print("\n--- Resultados de la Clasificación ---")
            for filename, info in results.items():
                print(f"{filename} -> Clase: {info['clase']:<20} (Confianza: {info['confianza']}). C_healthy: {info['confianza healthy']}")
        else:
            print(f"No se encontraron imágenes válidas en {PATH_FOTOS}")

        # --- 5. CONTEO DE RESULTADOS
        print("\n--- Resumen de Clasificación ---")

        # 1. Inicializar los diccionarios para conteo y listas
        class_counts = {class_name: 0 for class_name in CLASES_DEL_MODELO}
        class_file_lists = {class_name: [] for class_name in CLASES_DEL_MODELO}

        # También añadimos una categoría para 'Error'
        class_counts['Error'] = 0
        class_file_lists['Error'] = []

        # 2. Iterar sobre los resultados y poblar los diccionarios
        for filename, info in results.items():
            predicted_class = info['clase']

            # Verificamos si la clase predicha es una de las que esperamos
            if predicted_class in class_counts:
                class_counts[predicted_class] += 1
                class_file_lists[predicted_class].append(filename)
            else:
                # Si el modelo predice algo inesperado (no debería pasar)
                print(f"Advertencia: Clase '{predicted_class}' no reconocida.")

        print(class_file_lists)

        # 3. Mostrar los conteos finales
        print("\n--- Conteo Total por Clase ---")
        for class_name, count in class_counts.items():
            print(f"Total {class_name}: {count} imágenes")

        # --- 6. MOSTRAR HISTOGRAMA (NUEVO BLOQUE) ---
        print("\n--- Generando Histograma de Conteos ---")

        # 1. Preparar los datos para el histograma
        # Excluir 'Error' si no quieres graficarlo (opcional)
        # class_counts_filtered = {k: v for k, v in class_counts.items() if k != 'Error'}

        # Usaremos el diccionario completo por ahora
        clases = list(class_counts.keys())
        conteos = list(class_counts.values())

        # 2. Crear la gráfica
        plt.figure(figsize=(10, 6))

        # Crear la gráfica de barras
        bar_plot = plt.bar(clases, conteos, color='skyblue')

        # Añadir el número exacto (conteo) encima de cada barra
        for bar in bar_plot:
            height = bar.get_height()
            plt.text(bar.get_x() + bar.get_width() / 2.0, height,
                     f'{height}', ha='center', va='bottom')

        # 3. Añadir títulos y etiquetas
        plt.title('Histograma de Clases Predichas')
        plt.xlabel('Clase')
        plt.ylabel('Número de Imágenes')

        # Rotar las etiquetas del eje X si son largas
        plt.xticks(rotation=45, ha='right')

        # Ajustar el layout para que no se corten las etiquetas
        plt.tight_layout()

        # 4. Mostrar la gráfica
        plt.show()

    except Exception as e:
        print(f"\nHa ocurrido un error fatal: {e}")
        print("Verifica que las rutas, el número de clases y los nombres de las clases sean correctos.")