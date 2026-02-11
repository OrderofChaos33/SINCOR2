"use client";

import React, { useState } from 'react';
import { 
  Activity, 
  ShieldCheck, 
  BrainCircuit, 
  Wallet, 
  AlertTriangle, 
  GitBranch, 
  CheckCircle2, 
  Database,
  Terminal
} from 'lucide-react';
import { 
  BarChart, 
  Bar, 
  XAxis, 
  YAxis, 
  CartesianGrid, 
  Tooltip, 
  ResponsiveContainer,
  LineChart,
  Line
} from 'recharts';

// Mock Data from "Verify All Three" Session
const KERNEL_STATS = [
  { name: 'Goals', value: 1, full: 10 },
  { name: 'Plans', value: 7, full: 10 },
  { name: 'Agents', value: 1, full: 5 },
  { name: 'Steps', value: 3, full: 10 },
];

const CONTRACTS = [
  { name: "SINC Token", address: "0x5FbDB2315678afecb367f032d93F642f64180aa3", status: "Deployed (Local)" },
  { name: "Agent Token (SAGT)", address: "0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512", status: "Deployed (Local)" },
  { name: "Carbon Credit (SCC)", address: "0x9fE46736679d2D9a65F0992F2272dE9f3c7fa6e0", status: "Deployed (Local)" },
  { name: "Prediction Market", address: "0xCf7Ed3AccA5a467e9e704C703E8D87F634fB0Fc9", status: "Deployed (Local)" },
  { name: "Airdrop Manager", address: "0xDc64a140Aa3E981100a9becA4E685f962f0cF6C9", status: "Deployed (Local)" },
];

const LOGS = [
  { time: "15:20:01", type: "success", msg: "Agency Kernel (agency_kernel.py) verified." },
  { time: "15:19:55", type: "success", msg: "Compilation (24 contracts) successful." },
  { time: "15:19:10", type: "info", msg: "Redaction markers fixed in Python." },
  { time: "15:15:00", type: "error", msg: "Git pull failed: Unrelated histories." },
  { time: "15:25:30", type: "warning", msg: "Deploy paused: Insufficient ETH (0.0)." },
];

export default function Dashboard() {
  const [activeTab, setActiveTab] = useState('overview');

  return (
    <div className="min-h-screen bg-slate-950 text-slate-200 font-sans p-8">
      {/* Header */}
      <header className="mb-8 flex justify-between items-center border-b border-slate-800 pb-6">
        <div>
          <h1 className="text-3xl font-bold text-white flex items-center gap-3">
            <BrainCircuit className="h-8 w-8 text-indigo-500" />
            SINCOR Consensus Dashboard
          </h1>
          <p className="text-slate-400 mt-2">System State Snapshot • Feb 9, 2026</p>
        </div>
        <div className="flex gap-4">
          <div className="flex items-center gap-2 px-4 py-2 bg-emerald-500/10 border border-emerald-500/20 rounded-lg text-emerald-400">
            <ShieldCheck className="h-4 w-4" />
            <span>Environment Secure</span>
          </div>
          <div className="flex items-center gap-2 px-4 py-2 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400 animate-pulse">
            <Wallet className="h-4 w-4" />
            <span>0.00 ETH</span>
          </div>
        </div>
      </header>

      {/* Grid Layout */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        
        {/* Core Metrics */}
        <div className="col-span-2 space-y-6">
          {/* Stats Cards */}
          <div className="grid grid-cols-3 gap-4">
            <div className="bg-slate-900 border border-slate-800 p-4 rounded-xl">
              <div className="flex justify-between items-start mb-2">
                <span className="text-slate-400 text-sm">Reasoning Engine</span>
                <Activity className="h-4 w-4 text-indigo-400" />
              </div>
              <div className="text-2xl font-bold text-white">Operational</div>
              <div className="text-xs text-indigo-400 mt-1">Confidence Score: 0.95</div>
            </div>
            
            <div className="bg-slate-900 border border-slate-800 p-4 rounded-xl">
              <div className="flex justify-between items-start mb-2">
                <span className="text-slate-400 text-sm">Smart Contracts</span>
                <ShieldCheck className="h-4 w-4 text-emerald-400" />
              </div>
              <div className="text-2xl font-bold text-white">Compiled (24)</div>
              <div className="text-xs text-emerald-400 mt-1">OpenZeppelin v5 Ready</div>
            </div>
            
            <div className="bg-slate-900 border border-slate-800 p-4 rounded-xl">
              <div className="flex justify-between items-start mb-2">
                <span className="text-slate-400 text-sm">Repo Status</span>
                <GitBranch className="h-4 w-4 text-amber-400" />
              </div>
              <div className="text-2xl font-bold text-white">Diverged</div>
              <div className="text-xs text-amber-400 mt-1">Fix Pending (Unrelated History)</div>
            </div>
          </div>

          {/* Charts */}
          <div className="bg-slate-900 border border-slate-800 p-6 rounded-xl">
            <h3 className="text-lg font-semibold text-white mb-6">Kernel Execution Stats</h3>
            <div className="h-64 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={KERNEL_STATS}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                  <XAxis dataKey="name" stroke="#94a3b8" />
                  <YAxis stroke="#94a3b8" />
                  <Tooltip 
                    contentStyle={{ backgroundColor: '#1e293b', borderColor: '#334155', color: '#f1f5f9' }}
                  />
                  <Bar dataKey="value" fill="#6366f1" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* Contract List */}
          <div className="bg-slate-900 border border-slate-800 p-6 rounded-xl">
            <h3 className="text-lg font-semibold text-white mb-4">Deployed Contracts (Local Simulation)</h3>
            <div className="space-y-3">
              {CONTRACTS.map((c, i) => (
                <div key={i} className="flex items-center justify-between p-3 bg-slate-950 rounded-lg border border-slate-800">
                  <div className="flex items-center gap-3">
                    <Database className="h-4 w-4 text-slate-500" />
                    <div>
                      <div className="font-medium text-slate-200">{c.name}</div>
                      <div className="text-xs text-slate-500 font-mono">{c.address}</div>
                    </div>
                  </div>
                  <span className="text-xs px-2 py-1 bg-emerald-500/10 text-emerald-400 rounded">
                    {c.status}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Sidebar Log */}
        <div className="col-span-1 space-y-6">
          <div className="bg-slate-900 border border-slate-800 p-6 rounded-xl h-full">
            <h3 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
              <Terminal className="h-4 w-4" />
              System Event Log
            </h3>
            <div className="space-y-4">
              {LOGS.map((log, i) => (
                <div key={i} className="flex gap-3 items-start border-l-2 border-slate-700 pl-3 pb-4 last:pb-0">
                  <div className="mt-1">
                    {log.type === 'success' && <CheckCircle2 className="h-4 w-4 text-emerald-400" />}
                    {log.type === 'error' && <AlertTriangle className="h-4 w-4 text-red-400" />}
                    {log.type === 'warning' && <AlertTriangle className="h-4 w-4 text-amber-400" />}
                    {log.type === 'info' && <Terminal className="h-4 w-4 text-blue-400" />}
                  </div>
                  <div>
                    <div className="text-xs text-slate-500 font-mono mb-1">{log.time}</div>
                    <p className="text-sm text-slate-300">{log.msg}</p>
                  </div>
                </div>
              ))}
            </div>
            
            <div className="mt-8 pt-6 border-t border-slate-800">
              <h4 className="text-sm font-semibold text-slate-400 mb-3">Next Required Actions</h4>
              <ul className="space-y-2 text-sm">
                <li className="flex gap-2 items-center text-amber-400">
                  <div className="h-2 w-2 rounded-full bg-amber-400" />
                  Fund Deployer Wallet (0.05 ETH)
                </li>
                <li className="flex gap-2 items-center text-slate-400">
                  <div className="h-2 w-2 rounded-full bg-slate-600" />
                  Deploy to Base Sepolia
                </li>
                <li className="flex gap-2 items-center text-slate-400">
                  <div className="h-2 w-2 rounded-full bg-slate-600" />
                  Resolve Git History Divergence
                </li>
              </ul>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
