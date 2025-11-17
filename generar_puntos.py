
from geopy import distance
def generar_malla(esquinas, step=0.000020):
    """
    Genera una malla de puntos GPS dentro de un rectángulo definido por 4 esquinas.
    
    Parámetros:
        esquinas (list): Lista de 4 tuplas con coordenadas (lat, lon).
        step (float): Paso en grados (default=0.000020 ≈ ~2 m en latitud).
    
    Retorna:
        list: Lista de puntos (lat, lon) dentro de la malla.
    """
    # Determinar los límites
    latitudes = [p[0] for p in esquinas]
    longitudes = [p[1] for p in esquinas]

    lat_min, lat_max = min(latitudes), max(latitudes)
    lon_min, lon_max = min(longitudes), max(longitudes)

    # Generar la malla
    malla = []
    
    lat = lat_min
    #Ciclo while hasta que la latitud actual sea mayor a la latitud maxima
    while lat <= lat_max:
        #Lista para almacenar los puntos generados en el "eje" de la longitud
        grid_points = []
        lon = lon_min
        #Ciclo while hasta que la longitud actual sea mayor a la longitud maxima
        while lon <= lon_max:
            grid_points.append((lat, lon))
            #Se aumenta la longitud actual por 0.000020
            lon += step
        #Se aumenta la latitud actual por 0.000020
        lat += step
        #Se ingresan los puntos del "eje" de longitud a la matriz general de puntos
        malla.append(grid_points)

    return malla
