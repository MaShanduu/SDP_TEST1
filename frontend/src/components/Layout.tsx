import { Link, Outlet, useLocation } from "react-router-dom";

export default function Layout() {
  const { pathname } = useLocation();
  const onRepos = pathname === "/";

  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-20 border-b border-ink-800 bg-ink-950/85 backdrop-blur">
        <div className="mx-auto flex max-w-[1700px] items-center gap-4 px-4 py-3">
          <Link to="/" className="flex items-center gap-2" title="Repo Analysis Tool">
            <span className="grid h-8 w-8 place-items-center rounded-lg bg-accent/15 text-base text-accent">
              ◧
            </span>
            <span className="text-sm font-semibold tracking-wide text-white">RAT</span>
            <span className="hidden text-xs text-slate-500 sm:inline">Repo Analysis Tool</span>
          </Link>
          <nav className="ml-2 flex items-center gap-1">
            <Link to="/" className={onRepos ? "tab tab-active" : "tab"}>
              Repositories
            </Link>
          </nav>
          <div className="ml-auto hidden text-xs text-slate-500 md:block">
            COMS3011A · file / directory / repository / commit-set / author metrics
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-[1700px] flex-1 px-4 py-6">
        <Outlet />
      </main>

      <footer className="mx-auto w-full max-w-[1700px] px-4 pb-6 text-xs text-slate-600">
        Metrics follow the brief: file §2.1 · directory §2.2 · repository §2.3 · commit set §2.4 ·
        author §2.5. Renames are detected at 50% similarity; binary files are not measured.
      </footer>
    </div>
  );
}
