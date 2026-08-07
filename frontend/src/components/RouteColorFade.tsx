import { useEffect, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';

type Theme = 'warm' | 'night';

function themeFromPath(pathname: string): Theme {
  return pathname.includes('/fresco') || pathname.includes('/eclat')
    ? 'night'
    : 'warm';
}

/**
 * Fondu plein écran aux couleurs de la destination
 * (chaud → Traduction, bleu nuit → Fresco).
 */
export function RouteColorFade() {
  const location = useLocation();
  const prevPath = useRef(location.pathname);
  const [pulse, setPulse] = useState(0);
  const [theme, setTheme] = useState<Theme>(() =>
    themeFromPath(location.pathname),
  );

  useEffect(() => {
    const next = themeFromPath(location.pathname);
    document.body.dataset.product =
      next === 'night' ? 'fresco' : 'translate';

    if (prevPath.current === location.pathname) return;
    prevPath.current = location.pathname;
    setTheme(next);
    setPulse((n) => n + 1);
  }, [location.pathname]);

  if (pulse === 0) {
    return null;
  }

  return (
    <div
      key={pulse}
      className={`toa-route-fade toa-route-fade--${theme}`}
      aria-hidden="true"
    >
      <div className="toa-route-fade__wash" />
      <div className="toa-route-fade__veil" />
    </div>
  );
}
