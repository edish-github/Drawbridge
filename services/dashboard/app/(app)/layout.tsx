/**
 * The console shell: a fixed frame with a scrolling interior.
 *
 * Applies to the signed-in application and nothing else. Sign-in, sign-up and the workspace
 * picker live outside this group, because a shell whose navigation points into a workspace is
 * the wrong thing to render to somebody who has not chosen one — or who has just been told they
 * are no longer a member.
 *
 * The interior is the only thing that scrolls. An operator working a queue moves between a dozen
 * screens in a session, and a shell that stays put means the navigation never reflows under the
 * cursor.
 */

import Nav from "../nav";
import { requirePrincipal } from "../../lib/guard";

export default async function ConsoleLayout({ children }: { children: React.ReactNode }) {
  // Resolved once here as well as in each page. Belt and braces on purpose: a page added later
  // that forgets its own guard still cannot render inside an unauthenticated shell.
  const principal = await requirePrincipal();

  return (
    <div className="frame">
      <div className="console">
        <Nav
          orgName={principal.orgName}
          userName={principal.name || principal.email}
          role={principal.role ?? "viewer"}
        />
        <main className="main">{children}</main>
      </div>
    </div>
  );
}
