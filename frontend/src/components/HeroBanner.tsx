import banniereImg from '../assets/baniere.png';

export function HeroBanner() {
  return (
    <header className="toa-hero-full" role="banner">
      <div className="toa-hero-full__img-wrap">
        <img
          src={banniereImg}
          alt="Toa - Traduction de mangas et manhwas"
          className="toa-pixel-img toa-hero-full__img"
        />
        <div className="toa-hero-full__dither toa-hero-full__dither--bottom" aria-hidden="true" />
        <div className="toa-hero-full__dither toa-hero-full__dither--sides" aria-hidden="true" />
      </div>
    </header>
  );
}
