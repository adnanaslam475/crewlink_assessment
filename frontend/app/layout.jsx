import "./globals.css";

export const metadata = {
  title: "CrewLink — Leadership",
  description: "Send a callout and watch who got it.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
