/** Évite de compter deux fois la même page si elle est déposée plusieurs fois. */
export function dedupeFiles(fileList: File[]): File[] {
  const seen = new Set<string>();
  const out: File[] = [];
  for (const file of fileList) {
    const key = `${file.name}:${file.size}:${file.lastModified}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(file);
  }
  return out;
}
