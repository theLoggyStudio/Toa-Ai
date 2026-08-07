import { Link } from 'react-router-dom';
import logoImg from '../assets/logo.png';
import logoFrescoImg from '../assets/icon-fresco.png';

type Props = {
  tagline?: string;
  product?: 'translate' | 'fresco';
};

export function Footer({
  tagline = 'Traduction manga & manhwa',
  product = 'translate',
}: Props) {
  const isFresco = product === 'fresco';
  const brandLogo = isFresco ? logoFrescoImg : logoImg;
  const brandAlt = isFresco ? 'Fresco' : 'Toa AI';
  const brandTo = isFresco ? '/fresco' : '/TOA.ai';

  return (
    <footer className="toa-footer-bar">
      <div className="toa-footer-bar__inner">
        <Link to={brandTo} className="toa-footer-bar__brand">
          <img
            src={brandLogo}
            alt={brandAlt}
            className="toa-pixel-img toa-footer-bar__logo"
          />
        </Link>
        <span className="toa-footer-bar__tagline">{tagline}</span>
        <div className="toa-footer-bar__links">
          <Link to="/TOA.ai">Traduction</Link>
          <Link to="/fresco">Fresco</Link>
        </div>
      </div>
    </footer>
  );
}
