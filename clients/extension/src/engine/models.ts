// Model weights live in IndexedDB on the device, downloaded once from the household server and
// checked against the manifest's sha256. Nothing is fetched from the model's original host.

const DB = "watchman";
const STORE = "models";

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

export async function sha256Hex(bytes: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

export async function loadCached(file: string, sha256: string): Promise<ArrayBuffer | null> {
  const db = await openDb();
  const bytes = await new Promise<ArrayBuffer | undefined>((resolve, reject) => {
    const req = db.transaction(STORE).objectStore(STORE).get(file);
    req.onsuccess = () => resolve(req.result as ArrayBuffer | undefined);
    req.onerror = () => reject(req.error);
  });
  db.close();
  if (!bytes) return null;
  return (await sha256Hex(bytes)) === sha256 ? bytes : null;
}

export async function storeModel(file: string, bytes: ArrayBuffer): Promise<void> {
  const db = await openDb();
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite");
    tx.objectStore(STORE).put(bytes, file);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
  db.close();
}

/** Returns verified model bytes: from the cache, or downloaded with `download` and then cached. */
export async function ensureModel(
  file: string,
  sha256: string,
  download: () => Promise<ArrayBuffer>,
): Promise<ArrayBuffer> {
  const cached = await loadCached(file, sha256);
  if (cached) return cached;
  const bytes = await download();
  const digest = await sha256Hex(bytes);
  if (digest !== sha256) throw new Error(`model ${file} hash mismatch: ${digest.slice(0, 12)}`);
  await storeModel(file, bytes);
  return bytes;
}
