export interface FavStation { name: string; key: string }
const FAVS = 'tt_fav_stations';
const HIST = 'tt_history';

function read(k: string): FavStation[] {
  try { return JSON.parse(localStorage.getItem(k) || '[]'); }
  catch { return []; }
}

export function getFavorites(): FavStation[] { return read(FAVS); }
export function getHistory(): FavStation[] { return read(HIST); }

export function isFavorite(key: string): boolean {
  return read(FAVS).some((f) => f.key === key);
}

export function toggleFavorite(f: FavStation) {
  let favs = read(FAVS);
  favs = favs.some((x) => x.key === f.key)
    ? favs.filter((x) => x.key !== f.key)
    : [f, ...favs].slice(0, 30);
  localStorage.setItem(FAVS, JSON.stringify(favs));
}

export function pushHistory(f: FavStation) {
  const h = [f, ...read(HIST).filter((x) => x.key !== f.key)].slice(0, 10);
  localStorage.setItem(HIST, JSON.stringify(h));
}
