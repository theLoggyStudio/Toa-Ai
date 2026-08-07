import { NavLink } from 'react-router-dom';
import banniereImg from '../assets/baniere.png';
import banniereFrescoImg from '../assets/baniere-fresco.png';

type Props = {
  product?: 'translate' | 'fresco';
};

export function HeroBanner({ product = 'translate' }: Props) {
  const isFresco = product === 'fresco';
  const bannerSrc = isFresco ? banniereFrescoImg : banniereImg;

  return (
    <header
      className={`toa-hero-full${isFresco ? ' toa-hero-full--fresco' : ''}`}
      role="banner"
    >
      <div className="toa-hero-full__img-wrap">
        <img
          src={bannerSrc}
          alt={
            isFresco
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
