import torch
import torchvision
import torchvision.transforms as transforms
import torchvision.models as models
from torch import nn
from typing import List
import tqdm
import numpy as np

device = "cuda" if torch.cuda.is_available() else "cpu"
# Define transforms for the dataset
transform_train = transforms.Compose([
    transforms.RandomCrop(32, padding=4),
    transforms.RandomHorizontalFlip(),
    transforms.ToTensor(),
    transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5)),
])

transform_test = transforms.Compose([
    transforms.ToTensor(),
    transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5)),
])

# Load CIFAR-100 dataset
trainset = torchvision.datasets.CIFAR100(root='./data', train=True, download=True, transform=transform_train)

# Split the training set into training and validation sets
train_size = int(0.8 * len(trainset))
val_size = len(trainset) - train_size
trainset, valset = torch.utils.data.random_split(trainset, [train_size, val_size])
trainloader = torch.utils.data.DataLoader(trainset, batch_size=128, shuffle=True, num_workers=2)
valloader = torch.utils.data.DataLoader(valset, batch_size=128, shuffle=True, num_workers=2)

testset = torchvision.datasets.CIFAR100(root='./data', train=False, download=True, transform=transform_test)
testloader = torch.utils.data.DataLoader(testset, batch_size=100, shuffle=False, num_workers=2)

# Define ResNet model
net = models.resnet50()
net.to(device=device)

# Define classifier model
class FeedForwardClassifier(torch.nn.Module):
    def __init__(self, output_dim, embed_size) -> None:
        super().__init__()
        self.fc1 = torch.nn.Linear(embed_size, 250)
        self.fc2 = torch.nn.Linear(250, output_dim)

    def forward(self, embedding):
        x = embedding.squeeze()
        x = torch.nn.ReLU()(self.fc1(x)).to(device)
        output = self.fc2(x)
        return output

    def predict(self, embedding):
        logits = self.forward(embedding)
        print(logits)
        probabilities = torch.nn.Softmax()(logits)
        return torch.argmax(probabilities, dim=1)

def train(resnet, num_classes = 100, num_epochs = 100, mode = "matryoshka", load_previous = False, cl_embed_size = 1000, batch_size = 128):
    resnet = resnet.to(device=device)
    # Define relative importance if mode == "matryoshka"
    if mode == "matryoshka":
        embed_logs = int(np.log(cl_embed_size)/np.log(2)) + 1 if np.log(cl_embed_size)/np.log(2) != int(np.log(cl_embed_size)/np.log(2)) else int(np.log(cl_embed_size)/np.log(2))
    else:
        embed_logs = 1         

    # Load old model if needed:
    if load_previous:
        resnet.load_state_dict(torch.load("../../best_resnet_model.pth"))
        classifiers = []
        for i in range(embed_logs):
            classifiers.append(FeedForwardClassifier(output_dim=num_classes, embed_size=min(2**(i+1), cl_embed_size)))
            classifiers[i].load_state_dict(torch.load("../../best_classifier_"+str(i+1)+".pth"))
            classifiers[i] = classifiers[i].to(device=device)
            classifiers[i].train()
    else:
        # Define the list of classifiers
        classifiers = []
        best_classifiers =[None for i in range(embed_logs)]
        for i in range(embed_logs):
            classifiers.append(FeedForwardClassifier(output_dim=num_classes, embed_size=min(2**(i+1), cl_embed_size)))
            classifiers[i] = classifiers[i].to(device=device)
            classifiers[i].train()

    # Define loss function
    criterion =  nn.CrossEntropyLoss().to(device=device)

    # Define optimizer
    optimizer = torch.optim.Adam(net.parameters(), lr=0.0003)

    m_acc = 0

    # Train the model
    for epoch in range(num_epochs):  # loop over the dataset multiple times
        resnet.train()
        for i in range(embed_logs):
            classifiers[i].train()
        running_loss = 0.0
        for i, data in tqdm.tqdm(enumerate(trainloader, 0), total=len(trainloader)):
            # get the inputs; data is a list of [inputs, labels]
            inputs, labels = data
            inputs = inputs.to(device)
            labels = labels.to(device)
            # zero the parameter gradients
            optimizer.zero_grad()
            outputs_ = resnet(inputs).to(device)
            for j in range(embed_logs):
                if j == 0:
                    temp_outputs = torch.zeros((len(inputs), num_classes))
                    temp_outputs = temp_outputs.to(device)
                outputs = classifiers[j](outputs_[:,:min(2**(j+1), cl_embed_size)])
                temp_outputs += outputs
            outputs = temp_outputs
            loss = criterion(outputs, labels)
            loss.backward()
            optimizer.step()
            # print statistics
            running_loss += loss.item()

        print('[%d, %5d] loss: %.3f' %
                (epoch + 1, i + 1, running_loss))
        for i in range(embed_logs):
            classifiers[i].eval()
        net.eval()

        # Validation
        curr_mean = 0
        for i in range(embed_logs):
            total = 0
            correct = 0   
            for j, data in enumerate(valloader):
                images, labels = data
                images = images.to(device)
                labels = labels.to(device)
                outputs = resnet(images)
                predicted = classifiers[i].predict(outputs[:, :min(2**(i+1), cl_embed_size)])
                total += labels.size(0)
                correct += (predicted == labels).sum().item()
            accuracy = correct/total*100
            print("Accuracy for classifier", i, ":", accuracy)
            curr_mean += accuracy

        curr_mean /= embed_logs

        # update model if accuracy is better
        if m_acc < curr_mean:
            m_acc = curr_mean
            best_model = net
            for i in range(embed_logs):
                best_classifiers[i] = classifiers[i]
                torch.save(best_classifiers[i].state_dict(), "../../best_classifier_"+str(i+1)+".pth")
            torch.save(best_model.state_dict(), "../../best_resnet_model.pth")

        print('Current Mean Accuracy:', curr_mean, 'Best Mean Accuracy:', m_acc)
    print('Finished Training')
    return best_model, best_classifiers, embed_logs

net, best_classifiers, embed_logs = train(net, num_classes = 100, num_epochs = 100, mode = "matryoshka", cl_embed_size = 1000)
# Test the model
net.eval()

with torch.no_grad():
    for i in range(embed_logs):
        correct = 0
        total = 0
        for data in testloader:
            images, labels = data
            inputs = inputs.to(device)
            labels = labels.to(device)
            outputs = net(images)
            predicted = best_classifiers[i].predict(outputs[:, :min(2**(i+1), 1000)])
            total += labels.size(0)
            correct += (predicted == labels).sum().item()
        print("Accuracy for classifier", i, ":", correct/total*100)

