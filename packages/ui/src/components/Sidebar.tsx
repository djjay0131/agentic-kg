'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  LayoutDashboard,
  FileQuestion,
  FileText,
  Upload,
  Network,
  Play,
} from 'lucide-react';

const navItems = [
  { href: '/', label: 'Dashboard', icon: LayoutDashboard },
  { href: '/problems', label: 'Problems', icon: FileQuestion },
  { href: '/papers', label: 'Papers', icon: FileText },
  { href: '/extract', label: 'Extract', icon: Upload },
  { href: '/graph', label: 'Graph', icon: Network },
  { href: '/workflows', label: 'Workflows', icon: Play },
];

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="w-64 bg-white border-r border-gray-200 min-h-screen">
      <div className="p-4 border-b border-gray-200">
        <h1 className="text-xl font-bold text-primary-600">Agentic KG</h1>
        <p className="text-sm text-gray-500">Knowledge Graph Explorer</p>
      </div>
      <nav className="p-2">
        {navItems.map((item) => {
          const isActive = pathname === item.href ||
            (item.href !== '/' && pathname.startsWith(item.href));
          const Icon = item.icon;

          return (
            <Link
              key={item.href}
              href={item.href}
              className={`flex items-center gap-3 px-4 py-3 rounded-lg mb-1 transition-colors ${
                isActive
                  ? 'bg-primary-50 text-primary-700 font-medium'
                  : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'
              }`}
            >
              <Icon size={20} />
              {item.label}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
