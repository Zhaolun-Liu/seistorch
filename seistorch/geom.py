import os
from copy import deepcopy
from typing import Tuple

import matplotlib.pyplot as plt
import numpy as np
import torch
from scipy.ndimage import gaussian_filter, gaussian_filter1d
from torch.nn.functional import conv2d

from .eqconfigure import Parameters
from .utils import load_file_by_type, to_tensor
from .siren import Siren


class WaveGeometry(torch.nn.Module):
    def __init__(self, domain_shape: Tuple, h: float, abs_N: int = 20, equation: str = "acoustic", multiple: bool = False):
        super().__init__()

        self.domain_shape = domain_shape

        self.multiple = multiple

        self.register_buffer("h", to_tensor(h))

        self.register_buffer("abs_N", to_tensor(abs_N, dtype=torch.uint8))

        # INIT boundary coefficients
        # self._init_b(abs_N)
        # self._init_cpml(abs_N)
        self.generate_pml_coefficients_2d(abs_N)
    
    def state_reconstruction_args(self):
        return {"h": self.h.item(),
                "abs_N": self.abs_N.item()}

    def __repr__(self):
        return "WaveGeometry shape={}, h={}".format(self.domain_shape, self.h)

    def forward(self):
        raise NotImplementedError("WaveGeometry forward() is not implemented. " \
                                  "Although WaveGeometry is a subclass of a torch.nn.Module, its forward() method should never be called. " \
                                  "It only exists as a torch.nn.Module to hook into pytorch as a component of a WaveCell.")

    @property
    def c(self):
        raise NotImplementedError

    @property
    def b(self):
        return self._b

    @property
    def d(self,):
        return self._d

    def _init_cpml(self, abs_N:int, f0:float=10.0, cp:float=1500., pa:float=1., pd:float=2., pb:float=1.):
        """Initialize the distribution of the d for unsplit PML"""
        """[1], Wei Zhang, Yang Shen, doi:10.1190/1.3463431"""
        self._init_d(abs_N, cp=cp, order=pd)
        #np.save("/home/wangsw/Desktop/wangsw/fwi/pmld.npy", self.d.cpu().detach().numpy())

    def _corners(self, abs_N, d, dx, dy, multiple=False):
        Nx, Ny = self.domain_shape
        for j in range(Ny):
            for i in range(Nx):
                # Left-Top
                if not multiple:
                    if i < abs_N+1 and j< abs_N+1:
                        if i < j: d[i,j] = dy[i,j]
                        else: d[i,j] = dx[i,j]
                # Left-Bottom
                if i > (Nx-abs_N-2) and j < abs_N+1:
                    if i + j < Nx: d[i,j] = dx[i,j]
                    else: d[i,j] = dy[i,j]
                # Right-Bottom
                if i > (Nx-abs_N-2) and j > (Ny-abs_N-2):
                    if i - j > Nx-Ny: d[i,j] = dy[i,j]
                    else: d[i,j] = dx[i,j]
                # Right-Top
                if not multiple:
                    if i < abs_N+1 and j> (Ny-abs_N-2):
                        if i + j < Ny: d[i,j] = dy[i,j]
                        else: d[i,j] = dx[i,j]

    def generate_pml_coefficients_2d(self, N=50, B=100.):
        Nx, Ny = self.domain_shape

        R = 10**(-((np.log10(N)-1)/np.log10(2))-3)
        #d0 = -(order+1)*cp/(2*abs_N)*np.log(R) # Origin
        R = 1e-6; order = 2; cp = 1000.# Mao shibo Master
        d0 = (1.5*cp/N)*np.log10(R**-1)
        d_vals = d0 * torch.linspace(0.0, 1.0, N + 1) ** order
        d_vals = torch.flip(d_vals, [0])

        d_x = torch.zeros(Ny, Nx)
        d_y = torch.zeros(Ny, Nx)
        
        if N > 0:
            d_x[0:N + 1, :] = d_vals.repeat(Nx, 1).transpose(0, 1)
            d_x[(Ny - N - 1):Ny, :] = torch.flip(d_vals, [0]).repeat(Nx, 1).transpose(0, 1)
            if not self.multiple:
                d_y[:, 0:N + 1] = d_vals.repeat(Ny, 1)
            d_y[:, (Nx - N - 1):Nx] = torch.flip(d_vals, [0]).repeat(Ny, 1)

        self.register_buffer("_d", torch.sqrt(d_x ** 2 + d_y ** 2).transpose(0, 1))
        self._corners(N, self._d, d_x.T, d_y.T, self.multiple)
        # np.save("/home/wangsw/inversion/2d/layer/results/l2/pml.npy", self._d.cpu().detach().numpy())

    def _init_d(self, abs_N, order:float = 2.0, cp:float = 1500.):

        Nx, Ny = self.domain_shape

        R = 10**(-((np.log10(abs_N)-1)/np.log10(2))-3)
        #d0 = -(order+1)*cp/(2*abs_N)*np.log(R) # Origin
        R = 1e-6; order = 2; cp = 3000.# Mao shibo Master
        d0 = (1.5*cp/abs_N)*np.log10(R**-1)
        d_vals = d0 * torch.linspace(0.0, 1.0, abs_N + 1) ** order
        d_vals = torch.flip(d_vals, [0])

        d_x = torch.zeros(Ny, Nx)
        d_y = torch.zeros(Ny, Nx)
        
        if abs_N > 0:
            d_x[0:abs_N + 1, :] = d_vals.repeat(Nx, 1).transpose(0, 1)
            d_x[(Ny - abs_N - 1):Ny, :] = torch.flip(d_vals, [0]).repeat(Nx, 1).transpose(0, 1)

            d_y[:, 0:abs_N + 1] = d_vals.repeat(Ny, 1)
            d_y[:, (Nx - abs_N - 1):Nx] = torch.flip(d_vals, [0]).repeat(Ny, 1)

        self.register_buffer("_d", torch.sqrt(d_x ** 2 + d_y ** 2).transpose(0, 1))
        self._corners(abs_N, self._d, d_x.T, d_y.T)

    def _init_b(self, abs_N: int, B:float = 100.0, mode = 'cosine'):
        """Initialize the distribution of the damping parameter for the PML"""

        Nx, Ny = self.domain_shape

        assert Nx > 2 * abs_N + 1, "The domain isn't large enough in the x-direction to fit absorbing layer. Nx = {} and N = {}".format(
            Nx, abs_N)
        assert Ny > 2 * abs_N + 1, "The domain isn't large enough in the y-direction to fit absorbing layer. Ny = {} and N = {}".format(
            Ny, abs_N)
            
        b_x = torch.zeros(Ny, Nx)
        b_y = torch.zeros(Ny, Nx)
                
        if mode == 'cosine':
            idx = (torch.ones(abs_N + 1) * (abs_N+1)  - torch.linspace(0.0, (abs_N+1), abs_N + 1))/(2*(abs_N+1))
            b_vals = torch.cos(np.pi*idx)
            b_vals = torch.ones_like(b_vals) * B * (torch.ones_like(b_vals) - b_vals)

            b_x[0:abs_N+1,:] = b_vals.repeat(Nx, 1).transpose(0, 1)
            b_x[(Ny - abs_N - 1):Ny, :] = torch.flip(b_vals, [0]).repeat(Nx, 1).transpose(0, 1)
            b_y[:, 0:abs_N + 1] = b_vals.repeat(Ny, 1)
            b_y[:, (Nx - abs_N - 1):Nx] = torch.flip(b_vals, [0]).repeat(Ny, 1)

        self.register_buffer("_b", torch.sqrt(b_x ** 2 + b_y ** 2).transpose(0, 1))    

class WaveGeometryFreeForm(WaveGeometry):
    def __init__(self, mode='forward', **kwargs):

        self.mode = mode
        self.autodiff = True

        h = kwargs['geom']['h']
        abs_N = kwargs['geom']['pml']['N']
        self.padding = kwargs['geom']['pml']['N']
        self.domain_shape = kwargs['domain_shape']
        self.dt = kwargs['geom']['dt']
        self.boundary_saving = kwargs['geom']['boundary_saving']
        self.device = kwargs['device']
        self.source_type = kwargs['geom']['source_type']
        self.receiver_type = kwargs['geom']['receiver_type']
        self.multiple = kwargs['geom']['multiple']
        self.model_parameters = []
        self.inversion = False
        self.kwargs = kwargs
        super().__init__(self.domain_shape, h, abs_N, multiple=kwargs['geom']['multiple'])
        self.equation = kwargs["equation"]
        self.use_implicit = kwargs["training"]['implicit']['use']
        # Initialize the model parameters if not using implicit neural network
        self._init_model(kwargs['VEL_PATH'], kwargs['geom']['invlist'])
        # Initialize the implicit neural network if using implicit neural network
        if self.use_implicit: self._init_siren()

    def _init_siren(self,):
        # inn stands for implicit neural network
        self.coords = self.get_mgrid_from_vel(self.domain_shape)
        self.siren = dict()
        for par in self.pars_need_invert:
            self.siren[par] = Siren(in_features=2, out_features=1, hidden_features=128,
                                    hidden_layers=4, outermost_linear=True)
            # load the pretrained model if it exists
            pretrained = self.kwargs['training']['implicit']['pretrained']
            if os.path.exists(pretrained):
                self.siren[par].load_state_dict(torch.load())
            else:
                print(f"Cannot find the pretrained model '{pretrained}'")
            # send the siren to the target device
            self.siren[par].to(self.device)

    def _init_model(self, modelPath: dict, invlist: dict):
        """Initilize the model parameters
        Args:
            modelPath (dict): The dictionary that contains the path of model files.
            invlist (dict): The dictionary that specify whether invert the model or not.
        """
        needed_model_paras = Parameters.valid_model_paras()[self.equation]
        self.true_models = dict()
        self.pars_need_invert = []
        for para in needed_model_paras:
            # check if the model of <para> is in the modelPath
            if para not in modelPath.keys():
                print(f"Model '{para}' is not found in modelPath")
                exit()
            # check if the model of <para> is in the invlist
            if para not in invlist.keys():
                print(f"Model '{para}' is not found in invlist")
                exit()
            # check the existence of the model file
            if not os.path.exists(modelPath[para]):
                print(f"Cannot find model file '{modelPath[para]}' which is needed by equation {self.equation}")
                exit()
            # add the model to the graph
            mname, mpath = para, modelPath[para]
            print(f"Loading model '{mpath}', invert = {invlist[mname]}")
            if invlist[mname]:
                self.pars_need_invert.append(mname)
            # add the model to a list for later use
            self.model_parameters.append(mname)
            # load the ground truth model for calculating the model error
            self.true_models[mname]=np.load(self.kwargs['geom']['truePath'][mname])
            # load the initial model for the inversion
            if not self.use_implicit:
                invert = False if self.mode=='forward' else invlist[mname]
                self.__setattr__(mname, self.add_parameter(mpath, invert))

        # Loop over all the model parameters (invert=True)
        # for par in self.pars_need_invert:
        #     # Adding support for implicit velocity model
        #     nz, nx = self.domain_shape
        #     model = self.siren(self.coords)[0].view(nz, nx)
        #     # an-ti normalization for getting the true values
        #     mean = 1000.
        #     std = 3000.
        #     model = model * std + mean
        #     self.__setattr__(par, model)

    def step_implicit(self,):
        coords = self.coords # x, y coordinates
        shape = self.domain_shape # shape of the model
        for par in self.pars_need_invert:
            par_value = self.siren[par](coords)[0].view(shape)
            # an-ti normalization for getting the true values
            setattr(self, par, self.anti_normalization(par_value))

    def step(self,):
        """
            Doing this step for each iteration.
        """
        # If we use implicit neural network for reparameterization, 
        # we need to reset model parameters by input the coords of 
        # the model to the implicit neural network.
        # e.g: vp = siren_vp(coords); 
        # e.g: vs = siren_vs(coords);
        if self.use_implicit:
            self.step_implicit()

    
    def anti_normalization(self, model, mean=3000., std=1000.):
        return model * std + mean
    
    def __repr__(self):
        return f"Paramters of {self.model_parameters} have been defined."

    # Add the torch paramter
    def add_parameter(self, path: str, requires_grad=False):
        """Read the model paramter and setting the attribute 'requires_grad'.

        Args:
            path (str): The path of the model file. 
            requires_grad (bool, optional): Wheter this parameter need to be inverted. Defaults to False.

        Returns:
            _type_: torch.nn.Tensor
        """
        model = self.pad(load_file_by_type(path), mode="edge")
        return torch.nn.Parameter(to_tensor(model), requires_grad=requires_grad)

    def pad(self, d: np.ndarray, mode='edge'):
        """Padding the model based on the PML width.

        Args:
            d (np.ndarray): The data need to be padded.
            mode (str, optional): padding mode. Defaults to 'edge'.

        Returns:
            np.ndarray: the data after padding.
        """
        mode_options = ['constant', 'edge', 'linear_ramp', 'maximum', 'mean', 'median', 'minimum', 'reflect', 'symmetric', 'wrap']
        padding = self.padding
        if mode in mode_options:
            top = 0 if self.multiple else padding
            return np.pad(d, ((top, padding), (padding,padding)), mode=mode)
        else: # Padding the velocity with random velocites
            return self.pad_model_with_random_values(d, padding)
        
    def pad_model_with_random_values(self, model, N):
        # 获取模型的形状
        nz, nx = model.shape

        # 找到模型的最大值和最小值
        # min_val = np.min(model)
        # max_val = np.max(model)
        min_val = 400
        max_val = np.min(model)

        # 创建新的填充后的模型
        padded_model = np.zeros((nz + 2 * N, nx + 2 * N))

        # 将原来的模型复制到新的填充后的模型中心
        padded_model[N:N+nz, N:N+nx] = model

        # 在外侧填充随机值
        for i in range(N):
            padded_model[i, :] = np.random.uniform(min_val, max_val, (nx + 2 * N))  # 上侧
            padded_model[-i-1, :] = np.random.uniform(min_val, max_val, (nx + 2 * N))  # 下侧
            padded_model[:, i] = np.random.uniform(min_val, max_val, (nz + 2 * N))  # 左侧
            padded_model[:, -i-1] = np.random.uniform(min_val, max_val, (nz + 2 * N))  # 右侧

        return padded_model
    
    def tensor_to_img(self, key, array, padding=0, vmin=None, vmax=None, cmap="seismic"):
        cmap = plt.get_cmap(cmap)
        array = array[padding:-padding, padding:-padding]
        # 如果没有指定vmin和vmax，则使用数据的最小值和最大值
        if vmin is None:
            vmin = array.min()
        if vmax is None:
            vmax = array.max()
        
        # 截断vmin和vmax之外的值
        array = np.clip(array, vmin, vmax)
        
        # 将数据缩放到[0, 1]
        array = (array - vmin) / (vmax - vmin+1e-10)
        
        # 将数据转换为RGBA图像
        img = cmap(array)
        
        # 转换为PyTorch张量并添加批处理维度
        img = torch.from_numpy(img).permute(2, 0, 1)#.unsqueeze(0)

        return img
    
    def set_zero_boundaries(self, tensor, pad=50):
        tensor[..., :pad, :] = 0
        tensor[..., -pad:, :] = 0
        tensor[..., :, :pad] = 0
        tensor[..., :, -pad:] = 0
        return tensor

    def get_mgrid_from_vel(self, shape):
        '''Generates a flattened grid of (x,y,...) coordinates in a range of -1 to 1.
        sidelen: int
        dim: int'''
        nz, nx = shape
        xtensor = torch.linspace(-1, 1, steps=nx)
        ztensor = torch.linspace(-1, 1, steps=nz)
        mgrid = torch.stack(torch.meshgrid(ztensor, xtensor, indexing='ij'), dim=-1)
        mgrid = mgrid.reshape(-1, 2)
        return mgrid.to(self.device)

    def gradient_smooth(self, sigma=2):
        for para in self.model_parameters:
            var = self.__getattr__(para)
            if var.requires_grad:
                smoothed_grad = var.grad.cpu().detach().numpy()
                for i in range(10):
                    smoothed_grad = gaussian_filter1d(smoothed_grad, sigma, axis=1)
                    # smoothed_grad = gaussian_filter(smoothed_grad, sigma)
                var.grad.copy_(to_tensor(smoothed_grad).to(var.grad.device))

    def gradient_cut(self, mask=None, padding=50):
        top = 0 if self.multiple else padding
        mask = torch.nn.functional.pad(mask, (padding, padding, top, padding), mode='constant', value=0)
        for para in self.model_parameters:
            var = self.__getattr__(para)
            if var.requires_grad:
                var.grad.data = var.grad.data * mask
                # cut_grad = var.grad.cpu().detach().numpy()
                # cut_grad = self.set_zero_boundaries(cut_grad)
                # var.grad.copy_(to_tensor(cut_grad).to(var.grad.device))

    def reset_random_boundary(self,):
        for para in self.model_parameters:
            var = self.__getattr__(para).detach()
            if var.requires_grad:
                cut_var = var.cpu().detach().numpy()[self.padding:-self.padding, self.padding:-self.padding]
                pad_var = self.pad_model_with_random_values(cut_var, self.padding)
                var.copy_(to_tensor(pad_var).to(var.device))

    def save_model(self, path: str, paras: str, freq_idx=1, epoch=1, writer=None, max_epoch=1000):
        """Save the data of model parameters and their gradients(if they have).

        Args:
            path (str): The root save path.
            paras (str): not used.
            freq_idx (int, optional): The frequency index of multi scale. Defaults to 1.
            epoch (int, optional): The epoch of the current scale. Defaults to 1.
        """
        if not os.path.exists(path):
            os.makedirs(path, exist_ok=True)

        for para in self.model_parameters: # para is in ["vp", "vs", "rho", "Q", ....]
            #var = self.__getattr__(para)
            var = getattr(self, para)
            if para not in self.pars_need_invert:
                continue
            # if the model parameter is in the invlist, then save it.
            var_par = var.cpu().detach().numpy()
            if var.grad is not None:
                var_grad = var.grad.cpu().detach().numpy()
            else:
                var_grad = np.zeros_like(var_par)
            for key, data in zip(["para"+para, "grad"+para], [var_par, var_grad]):
                # Save the data of model parameters and their gradients(if they have) to disk.
                save_path = os.path.join(path, f"{key}F{freq_idx:02d}E{epoch:02d}.npy")
                np.save(save_path, data)

                # Calcualte the model error when the true model is known.
                if "para" in key and self.true_models:
                    _pad = self.padding
                    if self.multiple:
                        data = data[:-_pad, _pad:-_pad]
                    else:
                        data = data[_pad:-_pad, _pad:-_pad]
                    model_error = np.sum((data - self.true_models[para])**2)

                # Write the data to tensorboard.
                if writer is not None:
                    tensor_data = self.tensor_to_img(key, data, padding=self.padding, vmin=None, vmax=None)
                    # Write the model parameters and their gradients(if they have) to tensorboard.
                    writer.add_images(key, 
                                        tensor_data, 
                                        global_step=freq_idx*max_epoch+epoch, 
                                        dataformats='CHW',)
                    # Write the model error to tensorboard.
                    writer.add_scalar(f"model_error/{para}", model_error, global_step=freq_idx*max_epoch+epoch)
                