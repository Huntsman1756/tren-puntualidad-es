"""Tabla oficial INE: provincias (CPRO) y CCAA (CAUTO).

Los códigos coinciden con los dos primeros dígitos del código postal y con
la codificación oficial INE de provincias. Fuente: Instituto Nacional de
Estadística — relación provincia/CCAA. Tabla estable: no depende de
servicios externos ni cambia con el GTFS.
"""

# cpro -> (nombre oficial INE, código CCAA (CAUTO), nombre oficial CCAA)
PROVINCES = {
    "01": ("Araba/Álava", "16", "País Vasco"),
    "02": ("Albacete", "08", "Castilla-La Mancha"),
    "03": ("Alicante/Alacant", "10", "Comunitat Valenciana"),
    "04": ("Almería", "01", "Andalucía"),
    "05": ("Ávila", "07", "Castilla y León"),
    "06": ("Badajoz", "11", "Extremadura"),
    "07": ("Balears, Illes", "04", "Illes Balears"),
    "08": ("Barcelona", "09", "Cataluña"),
    "09": ("Burgos", "07", "Castilla y León"),
    "10": ("Cáceres", "11", "Extremadura"),
    "11": ("Cádiz", "01", "Andalucía"),
    "12": ("Castellón/Castelló", "10", "Comunitat Valenciana"),
    "13": ("Ciudad Real", "08", "Castilla-La Mancha"),
    "14": ("Córdoba", "01", "Andalucía"),
    "15": ("Coruña, A", "12", "Galicia"),
    "16": ("Cuenca", "08", "Castilla-La Mancha"),
    "17": ("Girona", "09", "Cataluña"),
    "18": ("Granada", "01", "Andalucía"),
    "19": ("Guadalajara", "08", "Castilla-La Mancha"),
    "20": ("Gipuzkoa", "16", "País Vasco"),
    "21": ("Huelva", "01", "Andalucía"),
    "22": ("Huesca", "02", "Aragón"),
    "23": ("Jaén", "01", "Andalucía"),
    "24": ("León", "07", "Castilla y León"),
    "25": ("Lleida", "09", "Cataluña"),
    "26": ("Rioja, La", "17", "La Rioja"),
    "27": ("Lugo", "12", "Galicia"),
    "28": ("Madrid", "13", "Comunidad de Madrid"),
    "29": ("Málaga", "01", "Andalucía"),
    "30": ("Murcia", "14", "Región de Murcia"),
    "31": ("Navarra", "15", "Comunidad Foral de Navarra"),
    "32": ("Ourense", "12", "Galicia"),
    "33": ("Asturias", "03", "Principado de Asturias"),
    "34": ("Palencia", "07", "Castilla y León"),
    "35": ("Palmas, Las", "05", "Canarias"),
    "36": ("Pontevedra", "12", "Galicia"),
    "37": ("Salamanca", "07", "Castilla y León"),
    "38": ("Santa Cruz de Tenerife", "05", "Canarias"),
    "39": ("Cantabria", "06", "Cantabria"),
    "40": ("Segovia", "07", "Castilla y León"),
    "41": ("Sevilla", "01", "Andalucía"),
    "42": ("Soria", "07", "Castilla y León"),
    "43": ("Tarragona", "09", "Cataluña"),
    "44": ("Teruel", "02", "Aragón"),
    "45": ("Toledo", "08", "Castilla-La Mancha"),
    "46": ("Valencia/València", "10", "Comunitat Valenciana"),
    "47": ("Valladolid", "07", "Castilla y León"),
    "48": ("Bizkaia", "16", "País Vasco"),
    "49": ("Zamora", "07", "Castilla y León"),
    "50": ("Zaragoza", "02", "Aragón"),
    "51": ("Ceuta", "18", "Ceuta"),
    "52": ("Melilla", "19", "Melilla"),
}

# variante textual del catálogo Renfe -> CPRO INE (todo normalizado:
# minúsculas y sin tildes)
PROV_ALIASES = {
    "albacete": "02", "alicante/alacant": "03", "alicante": "03",
    "alacant": "03", "almeria": "04", "araba/alava": "01", "araba": "01",
    "alava": "01", "asturias": "33", "avila": "05", "badajoz": "06",
    "barcelona": "08", "bizkaia": "48", "vizcaya": "48", "burgos": "09",
    "caceres": "10", "cadiz": "11", "cantabria": "39",
    "castellon/castello": "12", "castellon": "12", "castello": "12",
    "ciudad real": "13", "cordoba": "14", "coruna, a": "15",
    "a coruna": "15", "la coruna": "15", "coruna": "15", "cuenca": "16",
    "gipuzkoa": "20", "guipuzcoa": "20", "girona": "17", "gerona": "17",
    "granada": "18", "guadalajara": "19", "huelva": "21", "huesca": "22",
    "jaen": "23", "leon": "24", "lleida": "25", "lerida": "25",
    "lugo": "27", "madrid": "28", "malaga": "29", "murcia": "30",
    "navarra": "31", "ourense": "32", "orense": "32", "palencia": "34",
    "pontevedra": "36", "rioja, la": "26", "la rioja": "26", "rioja": "26",
    "salamanca": "37", "santa cruz de tenerife": "38", "tenerife": "38",
    "segovia": "40", "sevilla": "41", "soria": "42", "tarragona": "43",
    "teruel": "44", "toledo": "45", "valencia/valencia": "46",
    "valencia": "46", "valladolid": "47",
    "zamora": "49", "zaragoza": "50", "ceuta": "51", "melilla": "52",
    "las palmas": "35", "palmas, las": "35",
    "balears, illes": "07", "illes balears": "07", "baleares": "07",
    "mallorca": "07", "palma": "07",
}


def norm_text(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").lower())
                   if unicodedata.category(c) != "Mn").strip()


def province_code(name: str) -> str | None:
    """Nombre de provincia (catálogo Renfe, en mayúsculas) -> CPRO INE."""
    return PROV_ALIASES.get(norm_text(name))


def province_info(code: str):
    return PROVINCES.get(code)


def slug(name: str) -> str:
    """Slug estable para URLs: 'Castellón/Castelló' -> 'castellon'."""
    import re
    n = norm_text(name)
    n = n.replace("/", " ").replace(",", " ")
    n = re.sub(r"[^a-z0-9]+", "-", n).strip("-")
    # quitar comodines de orden: 'coruna-a' -> 'a-coruna'? mantener simple:
    return n


PROVINCE_SLUGS = {slug(v[0]): c for c, v in PROVINCES.items()}
# ajustes manuales de slug para URLs legibles
PROVINCE_SLUGS.update({
    "a-coruna": "15", "coruna": "15", "la-coruna": "15",
    "la-rioja": "26", "rioja": "26",
    "valencia": "46", "alicante": "03", "castellon": "12",
    "illes-balears": "07", "baleares": "07",
})

CCAA_SLUGS = {
    "andalucia": "01", "aragon": "02", "asturias": "03",
    "principado-de-asturias": "03", "illes-balears": "04",
    "baleares": "04", "canarias": "05", "cantabria": "06",
    "castilla-y-leon": "07", "castilla-la-mancha": "08",
    "cataluna": "09", "catalunya": "09",
    "comunitat-valenciana": "10", "valencia": "10",
    "extremadura": "11", "galicia": "12", "madrid": "13",
    "comunidad-de-madrid": "13", "murcia": "14",
    "region-de-murcia": "14", "navarra": "15",
    "pais-vasco": "16", "euskadi": "16", "la-rioja": "17",
    "rioja": "17", "ceuta": "18", "melilla": "19",
}
