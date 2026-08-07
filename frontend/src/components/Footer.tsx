import { Link } from 'react-router-dom';
import logoImg from '../assets/logo.png';

type Props = {
  tagline?: string;
};

export function Footer({ tagline = 'Traduction manga & manhwa' }: Props) {
  return (
    <footer className="toa-footer-bar">
      <div className="toa-footer-bar__inner">
        <Link to="/TOA.ai" className="toa-footer-bar__brand">
          <img
            src={logoImg}
            alt="Toa AI"
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
