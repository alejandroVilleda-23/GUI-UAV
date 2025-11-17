import os
from PIL import Image
import shutil


class ImagePatcher:
    def __init__(self, input_dir, output_dir, patch_size=224, overlap=0):
        """
        Inicializa el generador de recuadros (patches).

        Args:
            input_dir (str): Carpeta con las imágenes originales.
            output_dir (str): Carpeta donde se guardarán los recuadros.
            patch_size (int): Tamaño del recuadro (NxN). Por defecto 224.
            overlap (int): Píxeles de solapamiento entre recuadros.
                           0 = sin solapamiento (tipo grilla).
                           50 = se mueven solapándose un poco (mejor para no cortar lesiones).
        """
        self.input_dir = input_dir
        self.output_dir = output_dir
        self.patch_size = patch_size
        self.step = patch_size - overlap

        # Extensiones permitidas
        self.valid_extensions = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff'}

    def clean_output_folder(self):
        """Borra y recrea la carpeta de salida para evitar mezclar datos viejos."""
        if os.path.exists(self.output_dir):
            shutil.rmtree(self.output_dir)
        os.makedirs(self.output_dir)
        print(f"Carpeta limpia creada: {self.output_dir}")

    def process_folder(self):
        """Recorre la carpeta y procesa todas las imágenes."""
        if not os.path.exists(self.input_dir):
            print(f"Error: La carpeta de entrada '{self.input_dir}' no existe.")
            return

        self.clean_output_folder()

        files = os.listdir(self.input_dir)
        total_patches = 0
        processed_images = 0

        print(f"Iniciando procesamiento de {len(files)} archivos...")

        for filename in files:
            if os.path.splitext(filename)[1].lower() not in self.valid_extensions:
                continue

            img_path = os.path.join(self.input_dir, filename)
            patches_count = self._slice_image(img_path, filename)

            if patches_count > 0:
                processed_images += 1
                total_patches += patches_count
                print(f"Procesada: {filename} -> {patches_count} recuadros.")

        print("-" * 30)
        print(f"RESUMEN:")
        print(f"Imágenes procesadas: {processed_images}")
        print(f"Total de recuadros (224x224) generados: {total_patches}")
        print(f"Guardados en: {self.output_dir}")

    def _slice_image(self, img_path, filename):
        """Corta una sola imagen en recuadros."""
        try:
            with Image.open(img_path) as img:
                img = img.convert('RGB')  # Asegurar formato compatible
                img_w, img_h = img.size

                base_name = os.path.splitext(filename)[0]
                count = 0

                # Recorrer eje Y (Alto)
                for top in range(0, img_h, self.step):
                    # Recorrer eje X (Ancho)
                    for left in range(0, img_w, self.step):

                        # Definir coordenadas del recuadro (left, top, right, bottom)
                        right = left + self.patch_size
                        bottom = top + self.patch_size

                        # Verificar si el recuadro se sale de la imagen
                        if right > img_w or bottom > img_h:
                            # Opción A: Ignorar recuadros incompletos (Recomendado para ML estricto)
                            continue

                            # Opción B: Si quisieras incluir bordes incompletos,
                            # tendrías que añadir padding (relleno negro), pero
                            # para EfficientNet/DenseNet es mejor ignorar pedazos pequeños.

                        # Cortar
                        patch = img.crop((left, top, right, bottom))

                        # Guardar
                        # Nombre formato: original_Y_X.jpg (para saber de dónde vino)
                        save_name = f"{base_name}_patch_{top}_{left}.jpg"
                        save_path = os.path.join(self.output_dir, save_name)
                        patch.save(save_path, quality=95)
                        count += 1

                return count

        except Exception as e:
            print(f"Error al procesar {filename}: {e}")
            return 0


# --- BLOQUE DE EJECUCIÓN ---
if __name__ == "__main__":
    # 1. Configuración
    INPUT_FOLDER = "fotos_path_pruebas_4"  # Tu carpeta original
    OUTPUT_FOLDER = "fotos_path_pruebas_5"  # Nueva carpeta temporal para el modelo

    # 2. Crear instancia (Overlap=0 significa corte en cuadrícula exacta)
    # Si pones overlap=50, generará más fotos solapadas (útil si la enfermedad cae justo en el corte)
    patcher = ImagePatcher(
        input_dir=INPUT_FOLDER,
        output_dir=OUTPUT_FOLDER,
        patch_size=224,
        overlap=0
    )

    # 3. Ejecutar
    patcher.process_folder()