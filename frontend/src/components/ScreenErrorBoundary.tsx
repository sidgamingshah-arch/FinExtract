/** A screen that throws while rendering says so, instead of taking the whole app with it.
 *
 * WHY THIS EXISTS. There was no error boundary anywhere in this app, so React's default applied:
 * an exception thrown during render unmounts the entire tree. The cost of that was not
 * theoretical. The server had been sending a fifth `origin` value, `derived`, since c67e723;
 * `ORIGIN_CHIP` in Workspace.tsx is typed as total over the four the union then listed, so
 * `ORIGIN_CHIP[origin].help` read as safe and threw at runtime. Clicking the P&L tab blanked the
 * page — no message, no nav rail, no way back — on 6 of the 8 documents in the workspace. The
 * underlying gap was four characters wide. What made it a blank screen rather than a missing chip
 * was the absence of this file.
 *
 * MOUNTED INSIDE `RequireScreen`, so the boundary sits BELOW the TopBar and NavRail: whatever the
 * screen did, the reader keeps the chrome and can navigate somewhere else. A boundary around the
 * whole shell would have caught the same throw and still left them with nothing to click.
 *
 * RESET BY REMOUNTING. A caught boundary stays caught — React does not retry — so `RequireScreen`
 * passes the pathname as `resetKey`; changing it gives this component a new `key` and the next
 * screen mounts clean. Without that, one crash would follow the reader to every other screen for
 * the rest of the session.
 *
 * IT SHOWS THE ERROR TEXT. A boundary that renders "Something went wrong" and hides the message
 * turns a one-line diagnosis into a bug report, and this failure was diagnosable from its message
 * alone ("Cannot read properties of undefined (reading 'help')"). The stack goes to the console,
 * where a developer is already looking.
 */
import { Component, type ErrorInfo, type ReactNode } from "react";

import { Button, Card } from "./ui";
import { color, font } from "../theme";

interface Props {
  children: ReactNode;
  /** Changing this remounts the boundary and clears a caught error — see the note above. */
  resetKey?: string;
}

interface State {
  error: Error | null;
}

export class ScreenErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Kept on the console deliberately: this is the only full stack anyone gets, and the panel
    // below shows the message rather than the trace.
    console.error("[screen error]", error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return <>{this.props.children}</>;

    return (
      <div style={{ padding: "28px 32px", maxWidth: 900 }}>
        <Card>
          <div data-testid="screen-error" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <div style={{ fontSize: 15, fontWeight: 600, color: color.ink }}>
              This screen could not be displayed
            </div>
            <div style={{ fontSize: 12.5, color: color.sec2, lineHeight: 1.6 }}>
              The rest of the application is unaffected — the navigation on the left still works,
              and nothing you have extracted or reviewed has been lost. If it happens again, the
              message below is the useful part of a bug report.
            </div>
            <div
              data-testid="screen-error-message"
              style={{ fontFamily: font.mono, fontSize: 11.5, color: color.redFg,
                       background: color.redBg, padding: "10px 12px", borderRadius: 6,
                       whiteSpace: "pre-wrap", wordBreak: "break-word" }}
            >
              {error.message || String(error)}
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              {/* Remounts the children without a navigation, which is enough when the cause was
                  one bad response and the query has since been refetched. */}
              <Button testid="screen-error-retry" variant="secondary"
                      onClick={() => this.setState({ error: null })}>
                Try again
              </Button>
              <Button testid="screen-error-reload" variant="ghost"
                      onClick={() => window.location.reload()}>
                Reload the page
              </Button>
            </div>
          </div>
        </Card>
      </div>
    );
  }
}
