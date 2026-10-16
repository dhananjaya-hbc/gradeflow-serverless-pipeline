import { Link } from "react-router-dom";
import { useTitle } from "../hooks/useTitle";

export function NotFoundPage() {
  useTitle("Page not found");
  return (
    <section>
      <h1>Page not found</h1>
      <p>
        <Link to="/">Back to datasets</Link>
      </p>
    </section>
  );
}
