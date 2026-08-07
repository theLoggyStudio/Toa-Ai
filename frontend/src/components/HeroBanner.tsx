import { NavLink } from 'react-router-dom';
import banniereImg from '../assets/baniere.png';

type Props = {
  product?: 'translate' | 'fresco';
};

export function HeroBanner({ product = 'translate' }: Props) {
  return (
    <header className="toa-hero-full" role="banner">
      <div className="toa-hero-full__img-wrap">
        <img
          src={banniereImg}
          alt={
            product === 'fresco'
              ? 'Fresco - Restauration de photos'
              : 'Toa - Traduction de mangas et manhwas'
          }
          className="toa-pixel-img toa-hero-full__img"
        />
        <div className="toa-hero-full__dither toa-hero-full__dither--bottom" aria-hidden="true" />
        <div className="toa-hero-full__dither toa-hero-full__dither--sides" aria-hidden="true" />
      </div>
      <nav className="toa-product-nav" aria-label="Produits Toa AI">
        <NavLink
          to="/TOA.ai"
          className={({ isActive }) =>
            `toa-product-nav__link${isActive ? ' is-active' : ''}`
          }
        >
          Traduction
        </NavLink>
        <NavLink
          to="/fresco"
          className={({ isActive }) =>
            `toa-product-nav__link${isActive ? ' is-active' : ''}`
          }
        >
          Fresco
        </NavLink>
      </nav>
    </header>
  );
}
