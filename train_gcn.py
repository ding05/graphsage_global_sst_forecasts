import numpy as np
from numpy import asarray, save, load

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.optim import Adam
from torch_geometric.data import Data
from torch_geometric.nn import GCNConv, GATConv, SAGEConv, GatedGraphConv, RGCNConv

import time

data_path = 'data/'
models_path = 'configs/'
out_path = 'out/'

window_size = 12
lead_time = 1
learning_rate = 0.01
#learning_rate = 0.005 # for complex models
num_epochs = 30

# Load the data.

node_feats_grid = load('data/node_feats.npy')
print('Node feature grid in Kelvin:', node_feats_grid)
print('Shape:', node_feats_grid.shape)
print('----------')
print()

# Convert Kelvin to Celsius.
node_feats_grid -= 273.15
print('Node feature grid in Celsius:', node_feats_grid)
print('Shape:', node_feats_grid.shape)
print('----------')
print()

# Normalize the data to [-1, 1].
node_feats_grid_normalized = (node_feats_grid - np.min(node_feats_grid)) / (np.max(node_feats_grid) - np.min(node_feats_grid)) * 2 - 1
print('Normalized node feature grid:', node_feats_grid_normalized)
print('Shape:', node_feats_grid_normalized.shape)
print('----------')
print()

adj_mat = load('data/adj_mat_0.95.npy')
#adj_mat = load('data/adj_mat_0.9_directed.npy')
print('Adjacency matrix:', adj_mat)
print('Shape:', adj_mat.shape)
print('----------')
print()

# Compute the total number of time steps.
num_time = node_feats_grid.shape[1] - window_size - lead_time + 1

# Generate PyG graphs from NumPy arrays.

graph_list = []
for time_i in range(num_time):
    x = []
    y = []
    for node_i in range(node_feats_grid.shape[0]):
        # The inputs are normalized node features.
        x.append(node_feats_grid_normalized[node_i][time_i : time_i + window_size])
        # The outputs are node features in Celsius.
        y.append(node_feats_grid[node_i][time_i + window_size + lead_time - 1])
        '''
        # The outputs are normalized node features.
        y.append(node_feats_grid_normalized[node_i][time_i + window_size + lead_time - 1])
        '''
    x = torch.tensor(x)
    edge_index = torch.tensor(adj_mat, dtype=torch.long)
    data = Data(x=x, y=y, edge_index=edge_index, num_nodes=node_feats_grid.shape[0], num_edges=adj_mat.shape[1], has_isolated_nodes=True, has_self_loops=False, is_undirected=True)
    #data = Data(x=x, y=y, num_nodes=node_feats_grid.shape[0], num_edges=adj_mat.shape[1], has_isolated_nodes=True, has_self_loops=False, is_undirected=True)
    # If directed graphs
    #edge_attr = torch.ones(edge_index.shape[1], dtype=torch.float)
    #data = Data(x=x, y=y, edge_index=edge_index, edge_attr=edge_attr, num_nodes=node_feats_grid.shape[0], num_edges=adj_mat.shape[1], has_isolated_nodes=True, has_self_loops=False, is_undirected=False)
    graph_list.append(data)

# Set the number of decimals in torch tensors printed.
torch.set_printoptions(precision=8)

print('Inputs of the first node in the first graph, i.e. the first time step:', graph_list[0].x[0])
#print('Output of the first node in the first graph:', graph_list[0].y[0])
print('Check if they match those in the node features:', node_feats_grid[0][:13])
print('Check if they match those in the normalized node features:', node_feats_grid_normalized[0][:13])
print('----------')
print()

# Split the data, following Taylor & Feng, 2022.

train_graph_list = graph_list[:760]
val_graph_list = graph_list[760:]
test_graph_list = graph_list[760:]

# Set up the multi-graph GCN, using and modifying the code generated by ChatGPT-3.

class MultiGraphGCN(torch.nn.Module):
    def __init__(self, in_channels, hid_channels, out_channels, num_graphs):
        super(MultiGraphGCN, self).__init__()
        self.convs = torch.nn.ModuleList([torch.nn.Sequential(GCNConv(in_channels, hid_channels), GCNConv(hid_channels, out_channels)) for _ in range(num_graphs)])
        #self.convs = torch.nn.ModuleList([torch.nn.Sequential(GCNConv(in_channels, hid_channels), GCNConv(hid_channels, hid_channels), GCNConv(hid_channels, hid_channels), GCNConv(hid_channels, out_channels)) for _ in range(num_graphs)])
        self.double()
    def forward(self, data_list):
        x_list = []
        for i, data in enumerate(data_list):
            x = data.x
            for j, layer in enumerate(self.convs[i]):
                x = layer(x, data.edge_index)
                #x = F.tanh(x) # tanh() and sigmoid() suit y in [-1, 1].
                #x = F.relu(x) # relu() ant its variant suit y in a larger range.
                x = F.elu(x)
            x_list.append(x)
        x_concat = torch.cat(x_list, dim=0)
        return x_concat

class MultiGraphGAT(nn.Module):
    def __init__(self, in_channels, hid_channels, out_channels, num_heads, num_graphs):
        super(MultiGraphGAT, self).__init__()
        self.convs = torch.nn.ModuleList([torch.nn.Sequential(GATConv(in_channels, hid_channels, num_heads), GATConv(hid_channels * num_heads, out_channels, 1)) for _ in range(num_graphs)])
        #self.convs = torch.nn.ModuleList([torch.nn.Sequential(GATConv(in_channels, hid_channels, num_heads), GATConv(hid_channels * num_heads, hid_channels, num_heads), GATConv(hid_channels * num_heads, out_channels, 1)) for _ in range(num_graphs)])
        self.double()
    def forward(self, data_list):
        x_list = []
        for i, data in enumerate(data_list):
            x = data.x
            for j, layer in enumerate(self.convs[i]):
                x = layer(x, data.edge_index)
                x = F.elu(x)
            x_list.append(x)
        x_concat = torch.cat(x_list, dim=0)
        return x_concat

class MultiGraphSage(torch.nn.Module):
    def __init__(self, in_channels, hid_channels, out_channels, num_graphs):
        super(MultiGraphSage, self).__init__()
        self.convs = torch.nn.ModuleList([torch.nn.Sequential(SAGEConv(in_channels, hid_channels), SAGEConv(hid_channels, out_channels)) for _ in range(num_graphs)])
        self.double()
    def forward(self, data_list):
        x_list = []
        for i, data in enumerate(data_list):
            x = data.x
            for j, layer in enumerate(self.convs[i]):
                x = layer(x, data.edge_index)
                x = F.elu(x)
            x_list.append(x)
        x_concat = torch.cat(x_list, dim=0)
        return x_concat

class MultiGraphGGCN(torch.nn.Module):
    def __init__(self, in_channels, hid_channels, out_channels, num_graphs):
        super(MultiGraphGGCN, self).__init__()
        self.convs = torch.nn.ModuleList([torch.nn.Sequential(GatedGraphConv(in_channels, hid_channels), GatedGraphConv(hid_channels, out_channels)) for _ in range(num_graphs)])
        self.fc = nn.Linear(hid_channels, out_channels)
        self.double()
    def forward(self, data_list):
        x_list = []
        for i, data in enumerate(data_list):
            x = data.x
            for j, layer in enumerate(self.convs[i]):
                x = layer(x, data.edge_index)
                x = F.elu(x)
            x_list.append(x)
        x_concat = torch.cat(x_list, dim=0)
        out = self.fc(x_concat)
        return out

# If directed graphs
class MultiGraphRGCN(torch.nn.Module):
    def __init__(self, in_channels, hid_channels, out_channels, num_relations, num_bases):
        super(MultiGraphRGCN, self).__init__()
        #self.convs = torch.nn.ModuleList([torch.nn.Sequential(GCNConv(in_channels, hid_channels, num_relations, num_bases), GCNConv(hid_channels, hid_channels, num_relations, num_bases), GCNConv(hid_channels, out_channels, num_relations, num_bases))])
        self.convs = torch.nn.ModuleList([torch.nn.Sequential(GCNConv(in_channels, hid_channels, num_relations, num_bases), GCNConv(hid_channels, hid_channels, num_relations, num_bases), GCNConv(hid_channels, hid_channels, num_relations, num_bases), GCNConv(hid_channels, out_channels, num_relations, num_bases))])
        self.double()
    def forward(self, data_list):
        x_list = []
        for i, data in enumerate(data_list):
            x = data.x
            for j, layer in enumerate(self.convs[i]):
                x = layer(x, data.edge_index, data.edge_attr)
                x = F.elu(x)
            x_list.append(x)
        x_concat = torch.cat(x_list, dim=0)
        return x_concat

# Define the model.
#model = MultiGraphGCN(in_channels=graph_list[0].x[0].shape[0], hid_channels=30, out_channels=1, num_graphs=len(train_graph_list))
#model = MultiGraphGAT(in_channels=graph_list[0].x[0].shape[0], hid_channels=30, out_channels=1, num_heads=4, num_graphs=len(train_graph_list))
#model = MultiGraphSage(in_channels=graph_list[0].x[0].shape[0], hid_channels=30, out_channels=1, num_graphs=len(train_graph_list))
model = MultiGraphGGCN(in_channels=graph_list[0].x[0].shape[0], hid_channels=30, out_channels=1, num_graphs=len(train_graph_list))
# If directed graphs
#model = MultiGraphRGCN(in_channels=graph_list[0].x[0].shape[0], hid_channels=50, out_channels=1, num_relations=2, num_bases=4)

# Define the loss function.
criterion = nn.MSELoss()

# Define the optimizer.
#optimizer = Adam(model.parameters(), lr=0.01)
optimizer = torch.optim.RMSprop(model.parameters(), lr=learning_rate, alpha=0.9, weight_decay=0.01, momentum=0.9)

# Train a multi-graph GCN model, using and modifying the code generated by ChatGPT-3.

print('Start training.')
print('----------')
print()

# Start time
start = time.time()

# Record the results by epoch.
loss_epochs = []
val_mse_nodes_epochs = []

for epoch in range(num_epochs):
    # Iterate over the training data.
    for data in train_graph_list:
        optimizer.zero_grad()
        output = model([data])
        loss = criterion(output.squeeze(), torch.tensor(data.y).squeeze())
        loss.backward()
        optimizer.step()
    loss_epochs.append(round(loss.item(), 4))

    # Compute the MSE on the validation set.
    with torch.no_grad():
        val_mse_nodes = 0
        for data in val_graph_list:
            output = model([data])
            val_mse = criterion(output.squeeze(), torch.tensor(data.y).squeeze())
            print('Val predictions:', [round(i, 4) for i in output.squeeze().tolist()[::300]])
            print('Val observations:', [round(i, 4) for i in torch.tensor(data.y).squeeze().tolist()[::300]])
            val_mse_nodes += val_mse
        val_mse_nodes /= len(val_graph_list)
        val_mse_nodes_epochs.append(round(val_mse_nodes.item(), 4))
    print('----------')
    print()

    # Print the current epoch and validation MSE.
    print('Epoch [{}/{}], Loss: {:.4f}, Validation MSE: {:.4f}'.format(epoch + 1, num_epochs, loss.item(), val_mse_nodes))
    print('Loss by epoch:', loss_epochs)
    print('Validation MSE by epoch:', val_mse_nodes_epochs)

print('----------')
print()

# End time
stop = time.time()

print(f'Complete training. Time spent: {stop - start} seconds.')
print('----------')
print()

"""
# Test the model.
with torch.no_grad():
    test_mse_nodes = 0
    for data in test_graph_list:
        output = model([data])
        test_mse = criterion(output.squeeze(), torch.tensor(data.y).squeeze())
        print('Test predictions:', [round(i, 4) for i in output.squeeze().tolist()[::300]])
        print('Test observations:', [round(i, 4) for i in torch.tensor(data.y).squeeze().tolist()[::300]])
        test_mse_nodes += test_mse
    test_mse_nodes /= len(test_graph_list)
    print('Test MSE: {:.4f}'.format(test_mse_nodes))

print('----------')
print()
"""

# Save the model.
torch.save({
            'epoch': num_epochs,
            'model_state_dict': model.state_dict(),
            'optimizer_state_dict': optimizer.state_dict(),
            'loss': loss
            }, models_path + '_GCN_' + str(stop))

print('Save the checkpoint in a TAR file.')
print('----------')
print()
