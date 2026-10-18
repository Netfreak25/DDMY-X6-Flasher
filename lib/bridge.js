/** Pair the hosted UI only with an explicit helper on this computer. */
export function sessionFromLocation(location, storage) {
  const key = `x6-session:${location.pathname}`;
  const fragment = new URLSearchParams(location.hash.slice(1));
  let session = fragment.has('session') ? {token: fragment.get('session'), origin: fragment.get('bridge') || location.origin} :
    JSON.parse(storage.getItem(key) || 'null');
  if (!session) return null;
  const target = new URL(session.origin);
  if (target.protocol !== 'http:' || target.hostname !== '127.0.0.1' || target.username || target.password ||
      target.pathname !== '/' || target.search || target.hash || !session.token) {
    throw new Error('Ungültige Helfer-Sitzung. Bitte den Windows-Helfer erneut starten.');
  }
  session = {origin: target.origin, token: session.token};
  storage.setItem(key, JSON.stringify(session));
  return session;
}

/** The session token goes only to loopback, never to the public web server. */
export async function bridgeRequest(session, path, method = 'GET', binary = false) {
  if (!session) throw new Error('Bitte zuerst „DDMY X6 Helper.exe“ aus dem Windows-Helfer starten. Er öffnet diese Seite mit einer lokalen Sitzung.');
  const response = await fetch(`${session.origin}/api/${path}`, {
    method, headers: {'X-X6-Session': session.token}, credentials: 'omit', redirect: 'error',
  });
  if (!response.ok) throw new Error((await response.json()).error || 'Der lokale Helfer antwortet nicht.');
  return binary ? new Uint8Array(await response.arrayBuffer()) : response.json();
}
