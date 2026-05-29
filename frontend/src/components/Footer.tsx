import logoImg from '../assets/logo.png';

export function Footer() {
  return (
    <footer className="toa-footer-bar">
      <div className="toa-footer-bar__inner">
        <a href="/" className="toa-footer-bar__brand">
          <img
            src={logoImg}
            alt="Toa AI"
            className="toa-pixel-img toa-footer-bar__logo"
          />
        </a>
        <span className="toa-footer-bar__tagline">
          Traduction manga & manhwa
        </span>
      </div>
    </footer>
  );
}
