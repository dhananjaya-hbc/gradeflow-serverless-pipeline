import { NavLink, Outlet } from "react-router-dom";

export function Layout() {
  return (
    <>
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
      <main>
        <Outlet />
      </main>
    </>
  );
}
