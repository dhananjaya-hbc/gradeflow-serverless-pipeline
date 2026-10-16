import { NavLink, Outlet } from "react-router-dom";

export function Layout() {
  return (
    <>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="site-header">
        <NavLink to="/" className="brand">
          GradeFlow
        </NavLink>
        <nav aria-label="Main">
          <NavLink to="/" end>
            Datasets
          </NavLink>
          <NavLink to="/upload">Upload</NavLink>
        </nav>
      </header>
      <main id="main" tabIndex={-1}>
        <Outlet />
      </main>
    </>
  );
}
