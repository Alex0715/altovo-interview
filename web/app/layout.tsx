export const metadata = {
  title: "Altovo — Document Q&A",
  description: "Ask questions about your documents, with grounded citations.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body style={{ margin: 0 }}>{children}</body>
    </html>
  );
}
